"""Local services: launchd jobs (`launchctl list`) and TCP listeners (`lsof`).

Minimal first version; #3 extends it (plists, health URLs, restart counts). Resources:

- `service:launchd:<label>`  one per launchd job. up = has a PID; asleep = not running and the
  last exit was 0 (an on-demand or periodic job between runs); unknown = not running and it was
  last stopped by SIGTERM / SIGKILL (quit by the user, logout, memory pressure: can't tell from
  `launchctl list` whether it should be running); down = not running after any other exit.
- `service:port:<port>`      one per listening TCP port, always up while listed. A port that
  stops listening gets `gone_at`. `attrs.exposed` is true when it listens beyond loopback.

Jobs whose label starts with an ignored prefix are left out: Apple's own (`com.apple.`) and
per-launch app instances (`application.`), whose labels change every launch. Extra prefixes
can be listed in `<vault>/.kg/plane/config.yaml` under `services.ignore_prefixes`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from kg.plane.collectors import CollectContext, Collection, CommandError, register
from kg.plane.ids import is_valid_id, make_id
from kg.plane.rules import Verdict, rule
from kg.plane.store import Resource, Signal

DEFAULT_IGNORE = ("com.apple.", "application.")
_LOOPBACK = ("127.", "[::1]", "localhost")


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


def is_loopback(address: str) -> bool:
    return address.startswith(_LOOPBACK)


_STOPPED = (-15, -9)  # SIGTERM, SIGKILL as launchctl reports them


def job_status(job: LaunchdJob) -> str:
    if job.pid is not None:
        return "up"
    if job.last_exit in (0, None):
        return "asleep"
    return "unknown" if job.last_exit in _STOPPED else "down"


def launchd_items(jobs: list[LaunchdJob], ignore: tuple[str, ...], now: int
                  ) -> tuple[list[Resource], list[Signal], list[str]]:
    resources, signals, skipped = [], [], []
    for job in jobs:
        if job.label.startswith(ignore):
            continue
        rid = f"service:launchd:{job.label}"
        if not is_valid_id(rid):
            skipped.append(job.label)
            continue
        resources.append(Resource(rid, "service", job.label, "services",
                                  {"manager": "launchd", "label": job.label}))
        detail: dict[str, Any] = {"pid": job.pid, "last_exit": job.last_exit}
        signals.append(Signal(rid, now, job_status(job), detail=detail))
    return resources, signals, skipped


def port_items(listeners: list[Listener], now: int) -> tuple[list[Resource], list[Signal]]:
    by_port: dict[int, list[Listener]] = {}
    for item in listeners:
        by_port.setdefault(item.port, []).append(item)
    resources, signals = [], []
    for port, items in sorted(by_port.items()):
        rid = make_id("service", "port", str(port))
        commands = sorted({i.command for i in items if i.command})
        addresses = sorted({i.address for i in items})
        resources.append(Resource(
            rid, "service", f"{'/'.join(commands) or '?'} :{port}", "services",
            {"manager": "port", "port": port, "commands": commands, "addresses": addresses,
             "exposed": not all(is_loopback(a) for a in addresses)},
        ))
        signals.append(Signal(rid, now, "up", detail={"pids": sorted({i.pid for i in items})}))
    return resources, signals


def _ignore_prefixes(ctx: CollectContext) -> tuple[str, ...]:
    if ctx.vault is None:
        return DEFAULT_IGNORE
    file = ctx.vault / ".kg" / "plane" / "config.yaml"
    if not file.exists():
        return DEFAULT_IGNORE
    raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    extra = (raw.get("services") or {}).get("ignore_prefixes") or []
    return DEFAULT_IGNORE + tuple(str(p) for p in extra)


@register
class ServicesCollector:
    name = "services"

    def collect(self, ctx: CollectContext) -> Collection:
        out = Collection()
        scopes: list[str] = []
        try:
            jobs = parse_launchctl_list(ctx.run(["launchctl", "list"], timeout=15))
        except CommandError as exc:
            out.notes.append(f"launchctl 读不到，launchd 资源这轮不判 gone：{exc}")
        else:
            res, sig, skipped = launchd_items(jobs, _ignore_prefixes(ctx), ctx.now)
            out.resources += res
            out.signals += sig
            scopes.append("service:launchd:")
            if skipped:
                out.notes.append(f"{len(skipped)} 个 label 不能做资源 id，跳过")
        try:
            text = ctx.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pcn", "+c", "0"],
                           timeout=20)
        except CommandError as exc:
            out.notes.append(f"lsof 读不到，端口资源这轮不判 gone：{exc}")
        else:
            res, sig = port_items(parse_lsof_listen(text), ctx.now)
            out.resources += res
            out.signals += sig
            scopes.append("service:port:")
        out.scopes = tuple(scopes)
        return out


@rule("service", "service.down", window=2)
def service_down(history: list[Signal]) -> Verdict | None:
    """Open after 2 downs in a row, close on the first up; asleep / unknown change nothing."""
    last = history[-1]
    if last.status == "up":
        return Verdict(False, summary="恢复运行", evidence={"ts": last.ts})
    if len(history) >= 2 and all(s.status == "down" for s in history[-2:]):
        exits = [(s.detail or {}).get("last_exit") for s in history[-2:]]
        summary = f"连续 {len(history)} 次检查都没在运行（退出码 {exits[-1]}）"
        return Verdict(True, "critical", summary,
                       {"since": history[-2].ts, "ts": last.ts, "last_exit": exits})
    return None
