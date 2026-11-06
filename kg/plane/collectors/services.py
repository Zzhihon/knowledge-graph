"""Local services: launchd jobs, their plists, TCP listeners, and the owner's own registry.

Sources, all read-only:

- `launchctl list`                       which jobs are loaded, their PID and last exit status.
- `launchctl print gui/<uid>/<label>`    per loaded job: how many times launchd started it
                                         (`runs`, for restart loops) and its plist path.
- `~/Library/LaunchAgents/*.plist`       label, program, log paths, KeepAlive / StartInterval /
                                         StartCalendarInterval. Also lists agents that
                                         are not loaded.
- `ps -axo pid=,ppid=,etime=`            when each process started (= the last restart) and its
                                         parent, to tie a listening child process to its job.
- `lsof -nP -iTCP -sTCP:LISTEN`          port -> process.
- `<vault>/.kg/plane/services.yaml`      the owner's registry: what a service is for, its health
                                         URL, expected port, repo, group, what to ignore.
- `curl` to a registered health URL      only 127.0.0.1 / [::1]; latency and status code.

Resources:

- `service:launchd:<label>`  one per job: loaded, or a plist in ~/Library/LaunchAgents, or
                             registered.
- `service:port:<port>`      one per listening port; a registered port that stopped listening is
                             still reported (down), an unregistered one gets `gone_at`.

Each job has a mode. `keepalive` (KeepAlive true) must be running: no PID is `down`, except after
SIGTERM / SIGKILL, which is `unknown` (quit, logout, memory pressure). `scheduled`
(StartInterval / StartCalendarInterval) and `ondemand` (everything else) are `asleep` between
runs and `down` only when the last run exited non-zero. A registered job that is not loaded is
`down`; an unregistered plist that is not loaded is `unknown`. A running service whose expected
port is missing or whose health check fails is `degraded`.

Groups: `mine` (本人的), `tool` (工具的), `system` (系统的). Only `mine` alerts by default; the
registry can turn alerts on or off per service. Third-party ports are listed, never alerted on.
"""

from __future__ import annotations

import os
import plistlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import yaml

from kg.plane.collectors import CollectContext, Collection, CommandError, register
from kg.plane.ids import is_valid_id, make_id
from kg.plane.redact import redact
from kg.plane.rules import Verdict, rule
from kg.plane.store import Resource, Signal

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_IGNORE = ("com.apple.", "application.")
GROUPS = ("mine", "tool", "system")
_GROUP_ALIASES = {"本人": "mine", "本人的": "mine", "工具": "tool", "工具的": "tool",
                  "系统": "system", "系统的": "system"}
# A listener nobody registered, not under any launchd job, run by one of these is most likely
# the owner's own dev server: it counts as `mine` for the exposure reminder.
DEFAULT_MINE_COMMANDS = ("node", "python", "Python", "ruby", "deno", "bun", "java", "uvicorn",
                         "php", "go", "cargo")
_LOOPBACK = ("127.", "[::1]", "localhost")
_HEALTH_HOSTS = {"127.0.0.1", "::1"}
HEALTH_TIMEOUT = 3
EPHEMERAL_START = 49152  # macOS hands out ports from here on to anything that binds port 0
FLAP_WINDOW = 600  # seconds: "10 分钟内退出 ≥ 3 次"
FLAP_EXITS = 3
_STOPPED = (-15, -9)  # SIGTERM, SIGKILL as launchctl reports them


# ── parsing ──


@dataclass(frozen=True)
class LaunchdJob:
    label: str
    pid: int | None
    last_exit: int | None


@dataclass(frozen=True)
class Listener:
    port: int
    pid: int
    command: str
    address: str  # host part as lsof prints it: 127.0.0.1, [::1], *


@dataclass(frozen=True)
class Plist:
    label: str
    path: str
    program: tuple[str, ...] = ()
    logs: tuple[str, ...] = ()
    keepalive: bool = False
    start_interval: int | None = None
    calendar: bool = False
    disabled: bool = False

    @property
    def mode(self) -> str:
        if self.keepalive:
            return "keepalive"
        if self.start_interval or self.calendar:
            return "scheduled"
        return "ondemand"


def _int(raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        return None


def parse_launchctl_list(text: str) -> list[LaunchdJob]:
    """Rows of `launchctl list`: `PID<TAB>Status<TAB>Label`, `-` when there is none."""
    jobs: list[LaunchdJob] = []
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) != 3 or cols[0] == "PID":
            continue
        pid, status, label = (c.strip() for c in cols)
        if label:
            jobs.append(LaunchdJob(label, _int(pid), _int(status)))
    return jobs


def parse_lsof_listen(text: str) -> list[Listener]:
    """`lsof -nP -iTCP -sTCP:LISTEN -F pcn`: `p<pid>`, `c<command>`, `f<fd>`, `n<host:port>`."""
    listeners: list[Listener] = []
    pid, command = 0, ""
    for line in text.splitlines():
        tag, value = line[:1], line[1:]
        if tag == "p":
            pid, command = _int(value) or 0, ""
        elif tag == "c":
            command = value
        elif tag == "n":
            host, _, port = value.rpartition(":")
            number = _int(port.split()[0]) if port else None
            if host and number is not None:
                listeners.append(Listener(number, pid, command, host))
    return listeners


_RUNS = re.compile(r"^\s*runs = (\d+)\s*$", re.M)
_PATH = re.compile(r"^\s*path = (\S.*?)\s*$", re.M)


def parse_launchctl_print(text: str) -> tuple[int | None, str | None]:
    """(`runs`, plist `path`) from `launchctl print gui/<uid>/<label>`; None when absent."""
    runs = _RUNS.search(text)
    path = _PATH.search(text)
    return (int(runs.group(1)) if runs else None), (path.group(1) if path else None)


def parse_etime(raw: str) -> int | None:
    """`ps` elapsed time `[[dd-]hh:]mm:ss` -> seconds."""
    days, _, rest = raw.strip().rpartition("-")
    parts = rest.split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts) \
            or (days and not days.isdigit()):
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return seconds + int(days or 0) * 86400


def parse_ps(text: str) -> dict[int, tuple[int, int | None]]:
    """`ps -axo pid=,ppid=,etime=` -> {pid: (ppid, seconds since it started)}."""
    procs: dict[int, tuple[int, int | None]] = {}
    for line in text.splitlines():
        cols = line.split()
        if len(cols) != 3:
            continue
        pid, ppid = _int(cols[0]), _int(cols[1])
        if pid is not None and ppid is not None:
            procs[pid] = (ppid, parse_etime(cols[2]))
    return procs


def parse_plist(data: bytes, path: str) -> Plist | None:
    """One launchd plist (XML or binary); None when it is unreadable or has no Label."""
    try:
        raw = plistlib.loads(data)
    except Exception:  # noqa: BLE001 - plistlib raises several unrelated types
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("Label"), str):
        return None
    args = raw.get("ProgramArguments")
    program = tuple(str(a) for a in args) if isinstance(args, list) else ()
    if not program and isinstance(raw.get("Program"), str):
        program = (raw["Program"],)
    logs = tuple(dict.fromkeys(
        str(raw[k]) for k in ("StandardOutPath", "StandardErrorPath") if isinstance(raw.get(k), str)
    ))
    interval = raw.get("StartInterval")
    return Plist(
        label=raw["Label"], path=path, program=program, logs=logs,
        # KeepAlive can also be a dict of conditions (SuccessfulExit, NetworkState, ...):
        # then the job is allowed to be stopped, so it is not "must always run".
        keepalive=raw.get("KeepAlive") is True,
        start_interval=interval if isinstance(interval, int) and interval > 0 else None,
        calendar=bool(raw.get("StartCalendarInterval")),
        disabled=raw.get("Disabled") is True,
    )


def read_plists(folder: Path) -> tuple[dict[str, Plist], int]:
    """{label: plist} for `folder/*.plist`, and how many files could not be read."""
    found: dict[str, Plist] = {}
    bad = 0
    if not folder.is_dir():
        return found, 0
    for file in sorted(folder.glob("*.plist")):
        try:
            item = parse_plist(file.read_bytes(), str(file))
        except OSError:
            item = None
        if item is None:
            bad += 1
        else:
            found.setdefault(item.label, item)
    return found, bad


def is_loopback(address: str) -> bool:
    return address.startswith(_LOOPBACK)


# ── the owner's registry: <vault>/.kg/plane/services.yaml ──


@dataclass(frozen=True)
class Entry:
    key: str  # a launchd label, or `port:<n>` for a listener that is not a launchd job
    about: str = ""
    group: str | None = None
    repo: str = ""
    port: int | None = None
    health: str = ""
    expect: tuple[int, ...] = ()  # status codes that count as healthy; empty = 2xx / 3xx
    timeout: float = HEALTH_TIMEOUT
    alert: bool | None = None

    @property
    def bare_port(self) -> int | None:
        return _int(self.key[5:]) if self.key.startswith("port:") else None


@dataclass
class Registry:
    entries: dict[str, Entry] = field(default_factory=dict)
    groups: dict[str, tuple[str, ...]] = field(default_factory=dict)  # group -> label prefixes
    ignore: tuple[str, ...] = DEFAULT_IGNORE
    exposed_ok: frozenset[str] = frozenset()  # labels or `port:<n>` allowed beyond loopback
    mine_commands: tuple[str, ...] = DEFAULT_MINE_COMMANDS
    problems: list[str] = field(default_factory=list)


def _group(raw: Any) -> str | None:
    if raw is None:
        return None
    name = _GROUP_ALIASES.get(str(raw), str(raw))
    return name if name in GROUPS else None


def is_local_health_url(url: str) -> bool:
    """Health checks only ever go to this machine's loopback address, over plain HTTP(S)."""
    try:
        parts = urlsplit(url)
        return parts.scheme in ("http", "https") and parts.hostname in _HEALTH_HOSTS \
            and "@" not in parts.netloc
    except ValueError:
        return False


def _strings(raw: Any) -> tuple[str, ...]:
    return tuple(str(x) for x in raw) if isinstance(raw, list) else ()


def load_registry(vault: Path | None) -> Registry:
    """Read services.yaml (and the older `services.ignore_prefixes` in config.yaml)."""
    reg = Registry()
    if vault is None:
        return reg
    folder = vault / ".kg" / "plane"
    extra_ignore: list[str] = []
    config = folder / "config.yaml"
    if config.exists():
        try:
            raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
            extra_ignore += _strings((raw.get("services") or {}).get("ignore_prefixes"))
        except (yaml.YAMLError, AttributeError) as exc:
            reg.problems.append(f"config.yaml 读不懂，忽略：{exc}")
    file = folder / "services.yaml"
    if file.exists():
        try:
            raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raise TypeError("顶层不是映射")
        except (yaml.YAMLError, TypeError) as exc:
            reg.problems.append(f"services.yaml 读不懂，按没登记处理：{exc}")
            raw = {}
        groups = raw.get("groups") or {}
        if isinstance(groups, dict):
            for name, prefixes in groups.items():
                if name == "mine_commands":
                    reg.mine_commands = _strings(prefixes)
                elif _group(name):
                    reg.groups[_group(name) or name] = _strings(prefixes)
        ignore = raw.get("ignore") or {}
        if isinstance(ignore, dict):
            extra_ignore += _strings(ignore.get("labels"))
            reg.exposed_ok = frozenset(_strings(ignore.get("exposed")))
        services = raw.get("services") or {}
        if not isinstance(services, dict):
            reg.problems.append("services.yaml 的 services 不是映射，忽略")
            services = {}
        for key, item in services.items():
            key = str(key)
            item = item if isinstance(item, dict) else {}
            raw_health = item.get("health")
            spec = raw_health if isinstance(raw_health, dict) else {"url": raw_health}
            health = str(spec.get("url") or "")
            expect = tuple(c for c in spec.get("expect") or () if isinstance(c, int))
            timeout = spec.get("timeout")
            timeout = float(timeout) if isinstance(timeout, (int, float)) and 0 < timeout <= 30 \
                else HEALTH_TIMEOUT
            if health and not is_local_health_url(health):
                reg.problems.append(f"{key} 的健康检查地址不是 127.0.0.1，不打")
                health = ""
            if item.get("group") is not None and _group(item.get("group")) is None:
                reg.problems.append(f"{key} 的 group 只能是 mine / tool / system")
            port = item.get("port")
            alert = item.get("alert")
            reg.entries[key] = Entry(
                key, about=str(item.get("about") or ""), group=_group(item.get("group")),
                repo=str(item.get("repo") or ""), port=port if isinstance(port, int) else None,
                health=health, expect=expect, timeout=timeout,
                alert=alert if isinstance(alert, bool) else None)
    reg.ignore = DEFAULT_IGNORE + tuple(extra_ignore)
    return reg


def group_of(label: str, reg: Registry, *, user_agent: bool) -> str:
    entry = reg.entries.get(label)
    if entry and entry.group:
        return entry.group
    for name in GROUPS:
        if label.startswith(reg.groups.get(name, ())):
            return name
    # A plist the owner (or a tool they installed) put in ~/Library/LaunchAgents.
    return "tool" if user_agent else "system"


def _alert(entry: Entry | None, group: str) -> bool:
    if entry and entry.alert is not None:
        return entry.alert
    return group == "mine"


# ── status ──


def job_status(job: LaunchdJob | None, mode: str = "ondemand", *, registered: bool = False) -> str:
    if job is None:  # a plist that is not loaded
        return "down" if registered else "unknown"
    if job.pid is not None:
        return "up"
    if job.last_exit in _STOPPED:
        return "unknown"
    if mode == "keepalive":
        return "down"
    return "asleep" if job.last_exit in (0, None) else "down"


def probe(ctx: CollectContext, url: str, expect: Sequence[int] = (),
          timeout: float = HEALTH_TIMEOUT) -> dict[str, Any]:
    """One GET to a loopback health URL through curl (no proxy, no ~/.curlrc).

    Healthy is a 2xx / 3xx answer, or one of `expect` (e.g. 401 from an API that wants a key:
    it answered, so it is alive).
    """
    if not is_local_health_url(url):  # never leave the machine, whatever the caller passed
        return {"url": url, "code": 0, "ms": None, "ok": False, "error": "不是本机地址"}
    argv = ["curl", "-q", "-sS", "--noproxy", "*", "-o", "/dev/null",
            "-w", "%{http_code} %{time_total}", "--max-time", f"{timeout:g}", url]
    try:
        out = ctx.run(argv, timeout=timeout + 2).split()
    except CommandError as exc:
        return {"url": url, "code": 0, "ms": None, "ok": False, "error": str(exc)[:200]}
    code = _int(out[0]) if out else None
    try:
        ms = round(float(out[1]) * 1000, 1) if len(out) > 1 else None
    except ValueError:
        ms = None
    code = code or 0
    ok = code in expect if expect else 200 <= code < 400
    if expect and 200 <= code < 300:
        ok = True
    return {"url": url, "code": code, "ms": ms, "ok": ok}


def _owner(pid: int, job_pids: dict[int, str],
           procs: dict[int, tuple[int, int | None]]) -> str | None:
    """The launchd job a process belongs to: itself or the nearest ancestor that is a job."""
    for _ in range(32):
        if pid in job_pids:
            return job_pids[pid]
        parent = procs.get(pid, (1, None))[0]
        if parent <= 1 or parent == pid:
            return None
        pid = parent
    return None


@dataclass
class _Seen:
    jobs: list[LaunchdJob] | None = None
    plists: dict[str, Plist] = field(default_factory=dict)
    procs: dict[int, tuple[int, int | None]] = field(default_factory=dict)
    listeners: list[Listener] | None = None


def _launchd(ctx: CollectContext, seen: _Seen, reg: Registry, out: Collection,
             health: dict[str, dict[str, Any]]) -> dict[int, str]:
    """Resources for launchd jobs; returns {pid: label} of the running ones."""
    assert seen.jobs is not None
    loaded = {j.label: j for j in seen.jobs if not j.label.startswith(reg.ignore)
              or j.label in reg.entries}
    user_plists = {k: v for k, v in seen.plists.items() if not k.startswith(reg.ignore)
                   or k in reg.entries}
    registered = {k for k in reg.entries if not k.startswith("port:")}
    listening: dict[int, list[Listener]] = {}
    for item in seen.listeners or []:
        listening.setdefault(item.port, []).append(item)
    job_pids = {j.pid: j.label for j in loaded.values() if j.pid is not None}
    ports_of: dict[str, set[int]] = {}
    for item in seen.listeners or []:
        label = _owner(item.pid, job_pids, seen.procs)
        if label:
            ports_of.setdefault(label, set()).add(item.port)
    skipped = 0
    for label in sorted(set(loaded) | set(user_plists) | registered):
        rid = f"service:launchd:{label}"
        if not is_valid_id(rid):
            skipped += 1
            continue
        job = loaded.get(label)
        plist = user_plists.get(label)
        runs = None
        if job is not None:
            try:
                runs, path = parse_launchctl_print(
                    ctx.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], timeout=5))
            except CommandError:
                path = None
            if plist is None and path and path.endswith(".plist"):
                try:
                    plist = parse_plist(Path(path).read_bytes(), path)
                except OSError:
                    plist = None
        entry = reg.entries.get(label)
        mode = plist.mode if plist else "ondemand"
        group = group_of(label, reg, user_agent=label in seen.plists)
        status = job_status(job, mode, registered=label in registered)
        pid = job.pid if job else None
        started = None
        if pid is not None and pid in seen.procs and seen.procs[pid][1] is not None:
            started = ctx.now - (seen.procs[pid][1] or 0)
        port_ok = None
        if entry and entry.port is not None and seen.listeners is not None:
            port_ok = entry.port in listening
        probe_result = health.get(label)
        if status == "up" and (port_ok is False or (probe_result and not probe_result["ok"])):
            status = "degraded"
        attrs: dict[str, Any] = {
            "manager": "launchd", "label": label, "group": group, "mode": mode,
            "registered": entry is not None, "loaded": job is not None,
            "alert": _alert(entry, group),
        }
        if plist:
            attrs.update(plist=plist.path, logs=list(plist.logs),
                         command=redact(" ".join(plist.program))[:400],
                         start_interval=plist.start_interval, disabled=plist.disabled)
        if entry:
            attrs.update(about=entry.about, repo=entry.repo, port=entry.port,
                         health=entry.health or None)
        name = entry.about if entry and entry.about else label
        out.resources.append(Resource(rid, "service", name, "services", attrs))
        detail: dict[str, Any] = {
            "pid": pid, "last_exit": job.last_exit if job else None, "mode": mode,
            "loaded": job is not None, "alert": attrs["alert"], "group": group, "runs": runs,
            "started": started, "ports": sorted(ports_of.get(label, ())), "port_ok": port_ok,
            "expected_port": entry.port if entry else None, "health": probe_result,
        }
        value = probe_result.get("ms") if probe_result else None
        out.signals.append(Signal(rid, ctx.now, status, value, "ms" if value is not None else None,
                                  detail))
    if skipped:
        out.notes.append(f"{skipped} 个 label 不能做资源 id，跳过")
    return job_pids


def _ports(ctx: CollectContext, seen: _Seen, reg: Registry, out: Collection,
           job_pids: dict[int, str], health: dict[str, dict[str, Any]]) -> None:
    assert seen.listeners is not None
    by_port: dict[int, list[Listener]] = {}
    for item in seen.listeners:
        by_port.setdefault(item.port, []).append(item)
    registered_ports = {e.bare_port: e for e in reg.entries.values() if e.bare_port is not None}
    for port in sorted(set(by_port) | set(registered_ports)):
        rid = make_id("service", "port", str(port))
        key = f"port:{port}"
        entry = registered_ports.get(port)
        items = by_port.get(port, [])
        commands = sorted({i.command for i in items if i.command})
        addresses = sorted({i.address for i in items})
        owner = next((o for i in items if (o := _owner(i.pid, job_pids, seen.procs))), None)
        exposed = bool(items) and not all(is_loopback(a) for a in addresses)
        inferred = False
        if entry and entry.group:
            group = entry.group
        elif owner:
            group = group_of(owner, reg, user_agent=owner in seen.plists)
        elif reg.mine_commands and any(c.startswith(reg.mine_commands) for c in commands) \
                and (exposed or port < EPHEMERAL_START):
            # Ephemeral-range loopback ports of an interpreter are tool internals, not a server.
            group, inferred = "mine", True
        else:
            group = "system"
        exposed_ok = key in reg.exposed_ok or (owner is not None and owner in reg.exposed_ok)
        alert = entry.alert if entry and entry.alert is not None \
            else bool(entry) and group == "mine"
        name = entry.about if entry and entry.about else f"{'/'.join(commands) or '?'} :{port}"
        attrs: dict[str, Any] = {
            "manager": "port", "port": port, "commands": commands, "addresses": addresses,
            "exposed": exposed, "owner": owner, "group": group, "inferred": inferred,
            "registered": entry is not None, "alert": alert, "listening": bool(items),
        }
        if entry:
            attrs.update(about=entry.about, repo=entry.repo, health=entry.health or None)
        out.resources.append(Resource(rid, "service", name, "services", attrs))
        probe_result = health.get(key)
        status = "up" if items else "down"
        if status == "up" and probe_result and not probe_result["ok"]:
            status = "degraded"
        started = None
        pids = sorted({i.pid for i in items})
        if pids and pids[0] in seen.procs and seen.procs[pids[0]][1] is not None:
            started = ctx.now - (seen.procs[pids[0]][1] or 0)
        detail = {"pids": pids, "exposed": exposed, "exposed_ok": exposed_ok, "group": group,
                  "owner": owner, "alert": alert, "health": probe_result, "started": started,
                  "addresses": addresses}
        if not items:
            detail["reason"] = "port_missing"
        value = probe_result.get("ms") if probe_result else None
        out.signals.append(Signal(rid, ctx.now, status, value, "ms" if value is not None else None,
                                  detail))


@register
class ServicesCollector:
    name = "services"
    # Where the owner's agents live; None = ~/Library/LaunchAgents. Tests point it at a tmp dir.
    launch_agents_dir: Path | None = None

    def collect(self, ctx: CollectContext) -> Collection:
        out = Collection()
        reg = load_registry(ctx.vault)
        out.notes += reg.problems
        seen = _Seen()
        folder = self.launch_agents_dir or Path.home() / "Library" / "LaunchAgents"
        seen.plists, bad = read_plists(folder)
        if bad:
            out.notes.append(f"{bad} 个 plist 读不懂或没有 Label（launchd 也加载不了），跳过")
        try:
            seen.jobs = parse_launchctl_list(ctx.run(["launchctl", "list"], timeout=15))
        except CommandError as exc:
            out.notes.append(f"launchctl 读不到，launchd 资源这轮不判 gone：{exc}")
        try:
            seen.procs = parse_ps(ctx.run(["ps", "-axo", "pid=,ppid=,etime="], timeout=15))
        except CommandError as exc:
            out.notes.append(f"ps 读不到，这轮没有启动时间：{exc}")
        try:
            seen.listeners = parse_lsof_listen(ctx.run(
                ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pcn", "+c", "0"], timeout=20))
        except CommandError as exc:
            out.notes.append(f"lsof 读不到，端口资源这轮不判 gone：{exc}")
        health = {key: probe(ctx, e.health, e.expect, e.timeout)
                  for key, e in reg.entries.items() if e.health}
        scopes: list[str] = []
        job_pids: dict[int, str] = {}
        if seen.jobs is not None:
            job_pids = _launchd(ctx, seen, reg, out, health)
            scopes.append("service:launchd:")
        if seen.listeners is not None:
            _ports(ctx, seen, reg, out, job_pids, health)
            scopes.append("service:port:")
        out.scopes = tuple(scopes)
        return out


# ── rules ──
# Every rule closes when the resource stops alerting (`detail.alert` false), so turning a
# service's alerts off in services.yaml also clears what it had open. Signals written before
# #3 have no `alert` and count as alerting.


def _d(s: Signal) -> dict[str, Any]:
    return s.detail or {}


def _alerting(s: Signal) -> bool:
    return _d(s).get("alert", True) is not False


def _off(last: Signal) -> Verdict:
    return Verdict(False, summary="不再告警", evidence={"ts": last.ts, "reason": "alert_off"})


def _down_summary(last: Signal, count: int) -> tuple[str, str]:
    d = _d(last)
    if d.get("reason") == "port_missing":
        return "critical", f"登记的端口连续 {count} 次没在监听"
    if d.get("loaded") is False:
        return "critical", f"登记了，但连续 {count} 次没加载到 launchd"
    if d.get("mode") == "keepalive" or "mode" not in d:
        return "critical", f"连续 {count} 次检查都没在运行（退出码 {d.get('last_exit')}）"
    return "warning", f"上次运行失败（退出码 {d.get('last_exit')}），连续 {count} 次检查都是这样"


@rule("service", "service.down", window=2)
def service_down(history: Sequence[Signal]) -> Verdict | None:
    """Open after 2 downs in a row, close once it runs (up or degraded); asleep / unknown keep."""
    last = history[-1]
    if not _alerting(last):
        return _off(last)
    if last.status in ("up", "degraded"):
        return Verdict(False, summary="恢复运行", evidence={"ts": last.ts})
    if len(history) >= 2 and all(s.status == "down" for s in history[-2:]):
        exits = [_d(s).get("last_exit") for s in history[-2:]]
        severity, summary = _down_summary(last, len(history))
        evidence = {"since": history[-2].ts, "ts": last.ts, "last_exit": exits}
        for key in ("mode", "reason", "loaded"):
            if key in _d(last):
                evidence[key] = _d(last)[key]
        return Verdict(True, severity, summary, evidence)
    return None


@rule("service", "service.port_missing", window=2)
def service_port_missing(history: Sequence[Signal]) -> Verdict | None:
    """A running job whose registered port is not listening, twice in a row."""
    last = history[-1]
    if not _alerting(last):
        return _off(last)
    if _d(last).get("port_ok") is None or last.status == "down":
        return None  # nothing expected, or the process itself is down (service.down has it)
    port = _d(last).get("expected_port")
    if _d(last)["port_ok"]:
        return Verdict(False, summary=f"端口 {port} 恢复监听", evidence={"ts": last.ts})
    if len(history) >= 2 and all(_d(s).get("port_ok") is False for s in history[-2:]):
        return Verdict(True, "critical", f"进程在，但端口 {port} 连续 2 次没在监听",
                       {"since": history[-2].ts, "ts": last.ts, "port": port})
    return None


def _health(s: Signal) -> dict[str, Any] | None:
    h = _d(s).get("health")
    return h if isinstance(h, dict) else None


@rule("service", "service.health", window=2)
def service_health(history: Sequence[Signal]) -> Verdict | None:
    """Health check failed twice in a row while the process runs; one success closes."""
    last = history[-1]
    if not _alerting(last):
        return _off(last)
    h = _health(last)
    if h is None or last.status == "down":
        return None
    if h.get("ok"):
        return Verdict(False, summary=f"健康检查恢复（HTTP {h.get('code')}）",
                       evidence={"ts": last.ts, "code": h.get("code"), "ms": h.get("ms")})
    prev = _health(history[-2]) if len(history) >= 2 else None
    if prev is not None and not prev.get("ok") and history[-2].status != "down":
        code = h.get("code")
        why = f"HTTP {code}" if code else "连不上"
        return Verdict(True, "critical", f"健康检查连续 2 次失败（{why}）",
                       {"since": history[-2].ts, "ts": last.ts, "url": h.get("url"),
                        "codes": [prev.get("code"), code]})
    return None


def exits_between(a: Signal, b: Signal) -> int:
    """How many times the job exited between two readings.

    From launchd's `runs` counter when both have it (a drop means it was reloaded: 0);
    otherwise a changed PID, or a PID that went away, counts once.
    """
    ra, rb = _d(a).get("runs"), _d(b).get("runs")
    if isinstance(ra, int) and isinstance(rb, int):
        return max(rb - ra, 0)
    pa, pb = _d(a).get("pid"), _d(b).get("pid")
    return int(pa is not None and pa != pb)


@rule("service", "service.flapping", window=12)
def service_flapping(history: Sequence[Signal]) -> Verdict | None:
    """反复重启: a must-run job that exited 3+ times within 10 minutes.

    Only `keepalive` jobs: a scheduled job runs again and again on purpose. Closes once the
    readings of the last 10 minutes (spanning at least 5) show no exit at all.
    """
    last = history[-1]
    if not _alerting(last):
        return _off(last)
    if _d(last).get("mode") != "keepalive":
        return None
    recent = [s for s in history if s.ts >= last.ts - FLAP_WINDOW]
    exits = sum(exits_between(a, b) for a, b in zip(recent, recent[1:], strict=False))
    if exits >= FLAP_EXITS:
        minutes = (last.ts - recent[0].ts) // 60 or 1
        return Verdict(True, "warning", f"{minutes} 分钟内退出 {exits} 次，在反复重启",
                       {"since": recent[0].ts, "ts": last.ts, "exits": exits,
                        "runs": _d(last).get("runs"), "last_exit": _d(last).get("last_exit")})
    if exits == 0 and len(recent) >= 2 and last.ts - recent[0].ts >= FLAP_WINDOW // 2:
        return Verdict(False, summary="不再反复重启",
                       evidence={"ts": last.ts, "since": recent[0].ts})
    return None


@rule("service", "service.exposed", window=1)
def service_exposed(history: Sequence[Signal]) -> Verdict | None:
    """安全提醒: one of the owner's services listens beyond loopback (0.0.0.0, `*`, a LAN IP)."""
    d = _d(history[-1])
    if "exposed" not in d:
        return None  # not a port reading
    if d["exposed"] and d.get("group") == "mine" and not d.get("exposed_ok"):
        where = "、".join(a for a in d.get("addresses", []) if not is_loopback(a)) or "回环以外"
        return Verdict(True, "warning", f"监听在 {where}，局域网里的其他机器也能连上",
                       {"ts": history[-1].ts, "addresses": d.get("addresses"),
                        "owner": d.get("owner")})
    return Verdict(False, summary="只监听本机了" if not d["exposed"] else "已在配置里忽略",
                   evidence={"ts": history[-1].ts})
