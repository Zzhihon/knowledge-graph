"""Services on other machines in the tailnet, read over SSH: `kg plane collect remote`.

Config `<vault>/.kg/plane/remote.yaml` (template `remote.yaml.example`). Per host one
`ssh -o BatchMode=yes -o ConnectTimeout=8 <alias> /bin/sh -c '<script>'` runs a fixed
read-only script that prints one `@@<section> <service>` block per service:

- `launchctl print gui/<uid>/<label>`    state, pid, runs, last exit code.
- `stat -f %m <heartbeat>`               when the service last wrote its heartbeat file.
- `tail -n 20 <push ledger>`             JSONL records `{ts, ok, ch, ...}`, last pushes.
- `sqlite3 -readonly -json <db> <sql>`   optional: lowest airfare per route in the last day.

Only `/bin/sh`, `launchctl`, `stat`, `tail`, `date` and `/usr/bin/sqlite3` run on the remote:
a Mac without the Xcode Command Line Tools has `python3` / `git` stubs that pop an install
dialog, so the script never calls them.

Resources `service:remote:<host>:<label>`, kind `service`, so the `service.*` rules of
`services.py` judge them like local jobs (`service.down`: 2 downs in a row open, an up closes;
`service.flapping` from `runs`). Statuses:

  up        launchd job running and the heartbeat fresh
  degraded  running, but the last `push_fail_after` pushes on a watched channel all failed
  down      job not running, or its heartbeat older than `heartbeat_max_s`
  unknown   the host did not answer (a laptop that sleeps): never opens or closes anything

Rule `remote.push_failing` (warning) opens on a degraded push reading, closes on the next
successful push. The `fares` block of the latest signal feeds the daily brief.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from kg.plane.collectors import CollectContext, Collection, CommandError, register
from kg.plane.collectors.services import parse_launchctl_print
from kg.plane.ids import make_id
from kg.plane.rules import Verdict, rule
from kg.plane.store import Resource, Signal

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

SOURCE = "remote"
SSH_TIMEOUT = 30
HEARTBEAT_MAX_S = 180
PUSH_FAIL_AFTER = 2
FARES_LIMIT = 20
# Lowest price per route, departure date and return date (round trips carry `backDate` in
# `extra`; one-way rows get NULL) among rows fetched in the last day, with where it came from
# (the cheapest row; the newest of equal prices). A window function, not MIN() with bare
# columns: those follow the last aggregate once a query has two.
FARES_SQL = (
    "WITH t AS (SELECT *, CASE WHEN json_valid(extra) AND json_type(extra) = 'array' THEN "
    "(SELECT json_extract(je.value, '$.backDate') FROM json_each(extra) je "
    "WHERE json_extract(je.value, '$.tripType') = 'RT' LIMIT 1) END AS ret_date "
    "FROM flight_prices WHERE fetched_at >= datetime('now', 'localtime', '-1 day') "
    "AND depart_date >= date('now', 'localtime')) "
    "SELECT from_city, to_city, depart_date, ret_date, price, platform, airline, flight_no, "
    "fetched_at FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY from_city, to_city, "
    "depart_date, IFNULL(ret_date, '') ORDER BY price, fetched_at DESC) AS rn FROM t) "
    f"WHERE rn = 1 ORDER BY from_city, to_city, depart_date, ret_date LIMIT {FARES_LIMIT}"
)
# Route list with thresholds, read on the remote by the app's own Python so the rest of its
# config (bot tokens, API keys) never leaves the machine: only these keys are printed.
ROUTES_PY = (
    "import sys, json, yaml\n"
    "c = yaml.safe_load(open(sys.argv[1], encoding='utf-8')) or {}\n"
    "keys = ('from', 'to', 'from_name', 'to_name', 'dates', 'ret_dates', 'alert_direct')\n"
    "print(json.dumps([{k: r.get(k) for k in keys} for u in c.get('users') or []\n"
    "                  for r in u.get('routes') or [] if isinstance(r, dict)],"
    " ensure_ascii=False))\n"
)


# ── config: <vault>/.kg/plane/remote.yaml ──


@dataclass(frozen=True)
class RemoteService:
    label: str
    about: str = ""
    repo: str = ""
    dir: str = ""  # base for the relative paths below; `~/` is the remote home
    heartbeat: str = ""
    heartbeat_max_s: int = HEARTBEAT_MAX_S
    push_ledger: str = ""
    push_channels: tuple[str, ...] = ()  # empty = every channel
    push_fail_after: int = PUSH_FAIL_AFTER
    fares_db: str = ""
    routes_config: str = ""  # the app's config file; only routes and thresholds are read
    routes_python: str = ""  # a Python with PyYAML on the remote (the app's venv)
    alert: bool = True


@dataclass(frozen=True)
class RemoteHost:
    name: str  # resource id part and display name
    ssh: str  # ssh alias from ~/.ssh/config
    services: tuple[RemoteService, ...] = ()


@dataclass
class RemoteConfig:
    hosts: list[RemoteHost] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _path(base: str, raw: Any) -> str:
    path = str(raw or "")
    if not path or path.startswith(("/", "~")) or not base:
        return path
    return base.rstrip("/") + "/" + path


def _service(label: str, item: dict[str, Any]) -> RemoteService:
    base = str(item.get("dir") or "")
    push = item.get("push")
    push = push if isinstance(push, dict) else {}
    hb = item.get("heartbeat")
    hb = hb if isinstance(hb, dict) else {"path": hb}
    routes = item.get("routes")
    routes = routes if isinstance(routes, dict) else {}
    return RemoteService(
        label=label, about=str(item.get("about") or ""), repo=str(item.get("repo") or ""),
        dir=base, heartbeat=_path(base, hb.get("path")),
        heartbeat_max_s=int(hb.get("max_age_s") or HEARTBEAT_MAX_S),
        push_ledger=_path(base, push.get("ledger")),
        push_channels=tuple(str(c) for c in push.get("channels") or ()),
        push_fail_after=max(int(push.get("fail_after") or PUSH_FAIL_AFTER), 1),
        fares_db=_path(base, item.get("fares_db")),
        routes_config=_path(base, routes.get("config")),
        routes_python=_path(base, routes.get("python")),
        alert=item.get("alert", True) is not False)


def load_config(vault: Path | None) -> RemoteConfig:
    cfg = RemoteConfig()
    if vault is None:
        return cfg
    file = vault / ".kg" / "plane" / "remote.yaml"
    if not file.exists():
        return cfg
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise TypeError("顶层不是映射")
    except (yaml.YAMLError, TypeError) as exc:
        cfg.problems.append(f"remote.yaml 读不懂，这轮不采：{exc}")
        return cfg
    hosts = raw.get("hosts") or {}
    if not isinstance(hosts, dict):
        cfg.problems.append("remote.yaml 的 hosts 不是映射，忽略")
        return cfg
    for name, item in hosts.items():
        item = item if isinstance(item, dict) else {}
        services = item.get("services") or {}
        if not isinstance(services, dict) or not services:
            cfg.problems.append(f"{name} 没有登记 services，跳过")
            continue
        try:
            make_id("service", SOURCE, str(name), "x")
            parsed = tuple(_service(str(label), s if isinstance(s, dict) else {})
                           for label, s in services.items())
        except (ValueError, TypeError) as exc:
            cfg.problems.append(f"{name} 的配置不对，跳过：{exc}")
            continue
        cfg.hosts.append(RemoteHost(str(name), str(item.get("ssh") or name), parsed))
    return cfg


# ── the remote script and its output ──


def _q(path: str) -> str:
    """Quote a path for sh, keeping a leading `~/` as the remote $HOME."""
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def build_script(host: RemoteHost) -> str:
    lines = ["uid=$(id -u)", "echo \"@@now $(date +%s)\""]
    for s in host.services:
        tag = shlex.quote(s.label)
        lines.append(f"echo @@launchd {tag}; launchctl print \"gui/$uid/\"{tag} 2>/dev/null"
                     " | grep -E '^[[:space:]]*(state|pid|runs|last exit code) =' | head -8")
        if s.heartbeat:
            lines.append(f"echo @@heartbeat {tag}; stat -f %m {_q(s.heartbeat)} 2>/dev/null")
        if s.push_ledger:
            lines.append(f"echo @@push {tag}; tail -n 20 {_q(s.push_ledger)} 2>/dev/null")
        if s.fares_db:
            lines.append(f"echo @@fares {tag}; [ -f {_q(s.fares_db)} ] && /usr/bin/sqlite3"
                         f" -readonly -json {_q(s.fares_db)} {shlex.quote(FARES_SQL)} 2>&1")
        if s.routes_config and s.routes_python:
            lines.append(f"echo @@routes {tag}; [ -x {_q(s.routes_python)} ] && "
                         f"{_q(s.routes_python)} -I -c {shlex.quote(ROUTES_PY)}"
                         f" {_q(s.routes_config)} 2>&1")
    lines.append("echo @@end -")
    return "\n".join(lines) + "\n"


_SECTION = re.compile(r"^@@(\w+) (.+)$")
_FIELD = re.compile(r"^\s*(state|pid|last exit code) = (.+)$", re.M)


def parse_output(text: str) -> tuple[int | None, dict[str, dict[str, str]]]:
    """-> (remote now, {label: {section: body}}); a script cut short has no `@@end`."""
    now: int | None = None
    blocks: dict[tuple[str, str], list[str]] = {}
    current: list[str] | None = None
    ended = False
    for line in text.splitlines():
        m = _SECTION.match(line)
        if not m:
            if current is not None:
                current.append(line)
            continue
        section, label = m.groups()
        current = None
        if section == "now":
            now = int(label) if label.isdigit() else None
        elif section == "end":
            ended = True
        else:
            current = blocks.setdefault((label, section), [])
    if not ended:
        raise CommandError("远端脚本没跑完（输出缺 @@end）")
    out: dict[str, dict[str, str]] = {}
    for (label, section), body in blocks.items():
        out.setdefault(label, {})[section] = "\n".join(body).strip()
    return now, out


def parse_launchd(body: str) -> dict[str, Any]:
    fields: dict[str, str] = {}
    for key, value in _FIELD.findall(body):
        fields.setdefault(key, value.strip())  # first one is the job's; nested ones follow
    runs, _ = parse_launchctl_print(body)
    pid = fields.get("pid", "")
    code = fields.get("last exit code", "")
    return {"loaded": bool(body.strip()), "state": fields.get("state"),
            "pid": int(pid) if pid.isdigit() else None,
            "runs": runs,
            "last_exit": int(code) if re.fullmatch(r"-?\d+", code) else None}


def parse_pushes(body: str, channels: Sequence[str]) -> list[dict[str, Any]]:
    """Ledger records on the watched channels, oldest first; only ts / ok / ch are kept."""
    out = []
    for line in body.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or (channels and rec.get("ch") not in channels):
            continue
        out.append({"ts": str(rec.get("ts", "")), "ok": rec.get("ok") is True,
                    "ch": str(rec.get("ch", ""))})
    return out


def parse_fares(body: str) -> tuple[list[dict[str, Any]], str | None]:
    if not body:
        return [], None
    try:
        rows = json.loads(body)
    except ValueError:
        return [], body[:200]
    keep = ("from_city", "to_city", "depart_date", "ret_date", "price", "platform", "airline",
            "flight_no", "fetched_at")
    return [{k: r.get(k) for k in keep} for r in rows if isinstance(r, dict)], None


def parse_routes(body: str) -> tuple[list[dict[str, Any]], str | None]:
    """Routes and their direct-flight threshold (每人含税心理价, 0 = none)."""
    if not body:
        return [], None
    try:
        rows = json.loads(body)
    except ValueError:
        return [], body[:200]
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not r.get("from") or not r.get("to"):
            continue
        try:
            budget = float(r.get("alert_direct") or 0)
        except (TypeError, ValueError):
            budget = 0.0
        out.append({"from": str(r["from"]), "to": str(r["to"]),
                    "from_name": str(r.get("from_name") or r["from"]),
                    "to_name": str(r.get("to_name") or r["to"]),
                    "dates": [str(d) for d in r.get("dates") or []],
                    "ret_dates": [str(d) for d in r.get("ret_dates") or []],
                    "budget": budget})
    return out, None


def judge(s: RemoteService, sections: dict[str, str], now: int) -> tuple[str, dict[str, Any]]:
    job = parse_launchd(sections.get("launchd", ""))
    detail: dict[str, Any] = {"reachable": True, "group": "mine", "alert": s.alert,
                              "mode": "keepalive", **job}
    status = "up"
    if not job["loaded"]:
        status, detail["reason"] = "down", "not_loaded"
    elif job["pid"] is None:
        status, detail["reason"] = "down", "not_running"
    if s.heartbeat:
        raw = sections.get("heartbeat", "")
        age = now - int(raw) if raw.isdigit() else None
        detail["heartbeat_age_s"] = age
        if status == "up" and (age is None or age > s.heartbeat_max_s):
            status, detail["reason"] = "down", "heartbeat_stale"
            detail["heartbeat_max_s"] = s.heartbeat_max_s
    if s.push_ledger:
        pushes = parse_pushes(sections.get("push", ""), s.push_channels)
        tail = pushes[-s.push_fail_after:]
        failing = len(tail) == s.push_fail_after and not any(p["ok"] for p in tail)
        detail["push"] = {"last": pushes[-1] if pushes else None, "failing": failing,
                          "fails": [p["ts"] for p in tail if not p["ok"]]}
        if status == "up" and failing:
            status = "degraded"
    if s.fares_db:
        detail["fares"], err = parse_fares(sections.get("fares", ""))
        if err:
            detail["fares_error"] = err
    if s.routes_config and s.routes_python:
        detail["routes"], err = parse_routes(sections.get("routes", ""))
        if err:
            detail["routes_error"] = err
    return status, detail


@register
class RemoteCollector:
    name = SOURCE

    def collect(self, ctx: CollectContext) -> Collection:
        out = Collection()
        cfg = load_config(ctx.vault)
        out.notes += cfg.problems
        for host in cfg.hosts:
            sections: dict[str, dict[str, str]] = {}
            remote_now: int | None = None
            reason = ""
            try:
                text = ctx.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                                host.ssh, "/bin/sh -c " + shlex.quote(build_script(host))],
                               timeout=SSH_TIMEOUT)
                remote_now, sections = parse_output(text)
            except CommandError as exc:
                reason = str(exc)[:200]
                out.notes.append(f"{host.name} 连不上，这轮记 unknown：{reason}")
            now = remote_now or ctx.now
            for s in host.services:
                rid = make_id("service", SOURCE, host.name, s.label)
                out.resources.append(Resource(rid, "service", s.about or s.label, SOURCE,
                                              {"host": host.name, "label": s.label,
                                               "repo": s.repo, "about": s.about}))
                if reason:
                    out.signals.append(Signal(rid, ctx.now, "unknown", detail={
                        "reachable": False, "group": "mine", "alert": s.alert,
                        "reason": "host_unreachable", "error": reason}))
                    continue
                status, detail = judge(s, sections.get(s.label, {}), now)
                age = detail.get("heartbeat_age_s")
                out.signals.append(Signal(rid, ctx.now, status, age, "s" if age is not None
                                          else None, detail))
        return out


# ── rules ──


@rule("service", "remote.push_failing", window=1)
def remote_push_failing(history: Sequence[Signal]) -> Verdict | None:
    last = history[-1]
    push = (last.detail or {}).get("push")
    if not isinstance(push, dict):
        return None  # not a remote reading with a push ledger, or the host did not answer
    if (last.detail or {}).get("alert") is False:
        return Verdict(False, summary="不再告警", evidence={"ts": last.ts, "reason": "alert_off"})
    if push.get("failing"):
        return Verdict(True, "warning", f"最近 {len(push.get('fails') or [])} 次推送都失败了",
                       {"ts": last.ts, "fails": push.get("fails"), "last": push.get("last")})
    if (push.get("last") or {}).get("ok"):
        return Verdict(False, summary="推送恢复", evidence={"ts": last.ts,
                                                           "last": push.get("last")})
    return None


def fares_block(conn: Any) -> list[dict[str, Any]]:
    """For the brief: the lowest fares of every remote service that reports them."""
    from kg.plane.store import list_resources, list_signals

    out = []
    for res in list_resources(conn, kind="service", source=SOURCE):
        latest = list_signals(conn, res["id"], limit=1)
        detail = (latest[-1].detail or {}) if latest else {}
        if "fares" in detail or "fares_error" in detail:
            out.append({"resource": res["id"], "name": res["name"], "ts": latest[-1].ts,
                        "status": latest[-1].status, "fares": detail.get("fares", []),
                        **({"error": detail["fares_error"]} if "fares_error" in detail else {})})
    return out
