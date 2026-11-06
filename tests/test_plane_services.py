"""#3 local services: plists, registry, health checks, restart loops, exposure, log tails."""

from __future__ import annotations

import json
import plistlib
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kg.plane import store
from kg.plane.collectors import CollectContext, CommandError, get_collector, run_collector
from kg.plane.collectors.services import (
    LaunchdJob,
    ServicesCollector,
    exits_between,
    is_local_health_url,
    job_status,
    load_registry,
    parse_etime,
    parse_launchctl_print,
    parse_plist,
    parse_ps,
    probe,
)
from kg.plane.redact import MASK, redact
from kg.plane.rules import replay, rules_for
from kg.plane.store import Signal

if TYPE_CHECKING:
    from pathlib import Path

T0 = 1_762_000_000 - 1_762_000_000 % 3600
TWO_MIN = 120
WEB = "com.example.web"  # stands in for kg-web: registered, KeepAlive, port, health URL

PRINT_WEB = """gui/501/com.example.web = {
\tactive count = 1
\tpath = /tmp/agents/com.example.web.plist
\ttype = LaunchAgent
\tstate = running
\tstdout path = /tmp/logs/web.log
\truns = {runs}
\tpid = 4242
\tlast exit code = (never exited)
}
"""


def _plist(folder: Path, label: str, **keys: Any) -> Path:
    file = folder / f"{label}.plist"
    file.write_bytes(plistlib.dumps({"Label": label, **keys}))
    return file


@pytest.fixture
def agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "LaunchAgents"
    folder.mkdir()
    monkeypatch.setattr(ServicesCollector, "launch_agents_dir", folder)
    logs = tmp_path / "logs"
    logs.mkdir()
    _plist(folder, WEB, ProgramArguments=["/bin/zsh", "-lc", "exec ./serve --token=abc123secret"],
           KeepAlive=True, StandardOutPath=str(logs / "web.log"),
           StandardErrorPath=str(logs / "web.log"))
    _plist(folder, "com.example.nightly", ProgramArguments=["/usr/bin/true"],
           StartCalendarInterval={"Hour": 9})
    _plist(folder, "com.example.every-minute", ProgramArguments=["/usr/bin/true"], StartInterval=60)
    _plist(folder, "com.example.not-loaded", ProgramArguments=["/usr/bin/true"])
    _plist(folder, "homebrew.mxcl.db", ProgramArguments=["/opt/db"], KeepAlive=True)
    (folder / "broken.plist").write_text("not a plist", encoding="utf-8")
    return folder


def _vault(tmp_path: Path, text: str) -> Path:
    vault = tmp_path / "vault"
    (vault / ".kg" / "plane").mkdir(parents=True, exist_ok=True)
    (vault / ".kg" / "plane" / "services.yaml").write_text(text, encoding="utf-8")
    return vault


REGISTRY = f"""
groups:
  mine: [com.example.]
services:
  {WEB}:
    about: 网页
    repo: example-web
    port: 8765
    health: http://127.0.0.1:8765/ping
  port:9000:
    about: 本地开发服务
    group: mine
  com.example.gone-for-good:
    about: 登记了但从没装过
"""


class Machine:
    """Fake command outputs for one collect; tests change the fields between collects."""

    def __init__(self) -> None:
        self.web_pid: int | None = 4242
        self.web_exit = 0
        self.web_loaded = True
        self.runs = 1
        self.listening = {8765: ("python3.12", "127.0.0.1", 4243),
                          9000: ("node", "127.0.0.1", 7000)}
        self.health = "200 0.012"
        self.calls: list[list[str]] = []

    def launchctl_list(self) -> str:
        rows = ["PID\tStatus\tLabel", "-\t0\tcom.apple.Something"]
        if self.web_loaded:
            rows.append(f"{self.web_pid or '-'}\t{self.web_exit}\t{WEB}")
        rows += ["-\t0\tcom.example.nightly", "-\t1\tcom.example.every-minute",
                 "-\t-15\thomebrew.mxcl.db", "900\t0\tcom.vendor.helper"]
        return "\n".join(rows) + "\n"

    def run(self, argv: list[str], timeout: float = 20) -> str:
        self.calls.append(list(argv))
        if argv[:2] == ["launchctl", "list"]:
            return self.launchctl_list()
        if argv[:2] == ["launchctl", "print"]:
            if argv[2].endswith(WEB):
                return PRINT_WEB.replace("{runs}", str(self.runs))
            raise CommandError("launchctl print: not found")
        if argv[0] == "ps":
            # 4243 (the listener) is a child of the job's 4242.
            return ("  4242     1   01:02:03\n  4243  4242   01:00:00\n"
                    "   900     1 2-00:00:00\n  7000     1   05:00\n")
        if argv[0] == "lsof":
            out = []
            for port, (cmd, host, pid) in self.listening.items():
                out += [f"p{pid}", f"c{cmd}", "f3", f"n{host}:{port}"]
            return "\n".join(out) + "\n"
        if argv[0] == "curl":
            return self.health
        raise AssertionError(argv)


def _collect(machine: Machine, vault: Path | None, now: int = T0):
    ctx = CollectContext(now=now, vault=vault, run=machine.run)
    return get_collector("services").collect(ctx)


# ── parsing ──


def test_parse_plist_modes_and_logs(agents: Path) -> None:
    def read(label: str):  # type: ignore[no-untyped-def]
        return parse_plist((agents / f"{label}.plist").read_bytes(), "x")

    web = read(WEB)
    assert web is not None and web.mode == "keepalive" and len(web.logs) == 1  # same file twice
    assert read("com.example.nightly").mode == "scheduled"
    assert read("com.example.every-minute").mode == "scheduled"
    assert read("com.example.not-loaded").mode == "ondemand"
    # KeepAlive as a dict of conditions may legitimately be stopped.
    cond = plistlib.dumps({"Label": "a", "KeepAlive": {"SuccessfulExit": False}})
    assert parse_plist(cond, "x").mode == "ondemand"
    assert parse_plist(b"junk", "x") is None
    assert parse_plist(plistlib.dumps({"Program": "/bin/x"}), "x") is None  # no Label


def test_parse_launchctl_print_ps_and_etime() -> None:
    assert parse_launchctl_print(PRINT_WEB.replace("{runs}", "7")) == (
        7, "/tmp/agents/com.example.web.plist")
    assert parse_launchctl_print("nothing") == (None, None)
    assert parse_etime("05:00") == 300
    assert parse_etime("01:02:03") == 3723
    assert parse_etime("2-00:00:01") == 172801
    assert parse_etime("x") is None
    assert parse_ps("  1  0  10:00\n  9  1  00:05\nbad\n") == {1: (0, 600), 9: (1, 5)}


@pytest.mark.parametrize(("job", "mode", "registered", "status"), [
    (LaunchdJob("a", 1, 0), "keepalive", False, "up"),
    (LaunchdJob("a", None, 0), "keepalive", False, "down"),  # must run, exited cleanly: down
    (LaunchdJob("a", None, -15), "keepalive", False, "unknown"),  # SIGTERM: can't tell (#2)
    (LaunchdJob("a", None, -9), "scheduled", False, "unknown"),
    (LaunchdJob("a", None, 0), "scheduled", False, "asleep"),  # between runs
    (LaunchdJob("a", None, 2), "scheduled", False, "down"),  # last run failed
    (LaunchdJob("a", None, 0), "ondemand", False, "asleep"),
    (None, "keepalive", True, "down"),  # registered but not loaded
    (None, "keepalive", False, "unknown"),  # a plist nobody loaded
])
def test_job_status(job: LaunchdJob | None, mode: str, registered: bool, status: str) -> None:
    assert job_status(job, mode, registered=registered) == status


def test_registry_reads_groups_aliases_ignores_and_refuses_remote_health(tmp_path: Path) -> None:
    vault = _vault(tmp_path, """
groups:
  本人: [com.me.]
  tool: [homebrew.]
  mine_commands: [node]
ignore:
  labels: [com.noisy.]
  exposed: [port:5000, com.me.web]
services:
  com.me.web: {group: 工具的, health: "http://127.0.0.1:1/x", alert: true}
  com.me.api: {health: "http://example.com/health"}
  com.me.app: {health: {url: "http://[::1]:2/x", expect: [401], timeout: 9}, group: nope}
""")
    reg = load_registry(vault)
    assert reg.groups["mine"] == ("com.me.",) and reg.mine_commands == ("node",)
    assert "com.noisy." in reg.ignore and "com.apple." in reg.ignore
    assert reg.exposed_ok == {"port:5000", "com.me.web"}
    assert reg.entries["com.me.web"].group == "tool" and reg.entries["com.me.web"].alert is True
    assert reg.entries["com.me.api"].health == ""  # not loopback: never probed
    app = reg.entries["com.me.app"]
    assert (app.health, app.expect, app.timeout, app.group) == (
        "http://[::1]:2/x", (401,), 9.0, None)
    assert any("com.me.api" in p for p in reg.problems)
    assert any("com.me.app" in p and "group" in p for p in reg.problems)


def test_broken_registry_is_a_note_not_a_crash(tmp_path: Path) -> None:
    reg = load_registry(_vault(tmp_path, "services: [unclosed"))
    assert reg.entries == {} and reg.problems


def test_health_urls_must_be_loopback() -> None:
    assert is_local_health_url("http://127.0.0.1:8765/api")
    assert is_local_health_url("http://[::1]:8765/")
    for url in ("http://localhost:8765/", "http://192.168.1.2/", "http://user@127.0.0.1/",
                "file:///etc/passwd", "http://127.0.0.1.example.com/"):
        assert not is_local_health_url(url), url


def test_probe_goes_through_curl_without_proxy_and_judges_codes() -> None:
    seen: list[list[str]] = []

    def run(argv: list[str], timeout: float = 20) -> str:
        seen.append(argv)
        return {"a": "200 0.0123", "b": "401 0.002", "c": "000 0.001"}[argv[-1][-1]]

    ctx = CollectContext(now=T0, run=run)
    assert probe(ctx, "http://127.0.0.1:1/a") == {"url": "http://127.0.0.1:1/a", "code": 200,
                                                   "ms": 12.3, "ok": True}
    assert probe(ctx, "http://127.0.0.1:1/b")["ok"] is False
    assert probe(ctx, "http://127.0.0.1:1/b", expect=(401,))["ok"] is True
    assert probe(ctx, "http://127.0.0.1:1/c")["ok"] is False
    assert seen[0][:2] == ["curl", "-q"] and "--noproxy" in seen[0]
    assert probe(ctx, "http://10.0.0.1/a")["error"] == "不是本机地址"
    assert len(seen) == 4  # the remote one was never sent


# ── the collector ──


def test_collector_lists_plists_jobs_ports_with_groups_and_owners(
        agents: Path, tmp_path: Path) -> None:
    m = Machine()
    out = _collect(m, _vault(tmp_path, REGISTRY))
    res = {r.id: r for r in out.resources}
    sig = {s.resource_id: s for s in out.signals}
    status = {rid: s.status for rid, s in sig.items()}
    assert status == {
        f"service:launchd:{WEB}": "up",
        "service:launchd:com.example.nightly": "asleep",
        "service:launchd:com.example.every-minute": "down",  # scheduled, last run exited 1
        "service:launchd:com.example.not-loaded": "unknown",  # plist only, not registered
        "service:launchd:com.example.gone-for-good": "down",  # registered, nowhere to be found
        "service:launchd:homebrew.mxcl.db": "unknown",  # SIGTERM
        "service:launchd:com.vendor.helper": "up",
        "service:port:8765": "up",
        "service:port:9000": "up",
    }
    web = res[f"service:launchd:{WEB}"].attrs
    assert web["group"] == "mine" and web["mode"] == "keepalive" and web["alert"] is True
    assert web["about"] == "网页" and web["repo"] == "example-web" and web["registered"]
    assert web["logs"] == [str(tmp_path / "logs" / "web.log")]
    assert "abc123secret" not in web["command"] and MASK in web["command"]
    assert res["service:launchd:homebrew.mxcl.db"].attrs["group"] == "tool"  # user plist
    assert res["service:launchd:com.vendor.helper"].attrs["group"] == "system"  # no plist
    assert res["service:launchd:com.vendor.helper"].attrs["alert"] is False
    d = sig[f"service:launchd:{WEB}"].detail
    assert d["runs"] == 1 and d["ports"] == [8765] and d["port_ok"] is True
    assert d["started"] == T0 - 3723 and d["health"]["code"] == 200
    assert sig[f"service:launchd:{WEB}"].value == 12.0
    # The listener is a child of the job's process: the port belongs to the job.
    assert res["service:port:8765"].attrs["owner"] == WEB
    assert res["service:port:8765"].attrs["group"] == "mine"
    assert out.scopes == ("service:launchd:", "service:port:")
    assert any("plist" in n for n in out.notes)  # broken.plist
    printed = {c[2].rsplit("/", 1)[1] for c in m.calls if c[:2] == ["launchctl", "print"]}
    assert "com.example.not-loaded" not in printed and "com.apple.Something" not in printed
    assert WEB in printed


def test_missing_port_and_failing_health_degrade_a_running_job(
        agents: Path, tmp_path: Path) -> None:
    m = Machine()
    m.listening.pop(8765)
    m.listening.pop(9000)
    m.health = "503 0.004"
    out = _collect(m, _vault(tmp_path, REGISTRY))
    sig = {s.resource_id: s for s in out.signals}
    web = sig[f"service:launchd:{WEB}"]
    assert web.status == "degraded"
    assert web.detail["port_ok"] is False and web.detail["health"]["ok"] is False
    # A registered bare port that stopped listening is still reported, as down.
    assert sig["service:port:9000"].status == "down"
    assert sig["service:port:9000"].detail["reason"] == "port_missing"
    assert "service:port:8765" not in sig  # not registered as a bare port: it just goes


def test_unregistered_interpreter_on_all_interfaces_is_the_owners(
        agents: Path, tmp_path: Path) -> None:
    m = Machine()
    m.listening = {3000: ("node", "*", 7000), 5000: ("ControlCenter", "*", 7001),
                   61000: ("Python", "127.0.0.1", 7000), 8000: ("python3.12", "127.0.0.1", 7000)}
    sig = {s.resource_id: s for s in _collect(m, None).signals}
    assert sig["service:port:61000"].detail["group"] == "system"  # ephemeral loopback port
    assert sig["service:port:8000"].detail["group"] == "mine"  # a dev server port
    assert sig["service:port:3000"].detail["group"] == "mine"
    assert sig["service:port:3000"].detail["exposed"] is True
    assert sig["service:port:5000"].detail["group"] == "system"


def test_without_registry_nothing_alerts(agents: Path) -> None:
    out = _collect(Machine(), None)
    alerting = [r.id for r in out.resources if r.attrs.get("alert")]
    assert alerting == []
    groups = {r.attrs["group"] for r in out.resources if r.attrs["manager"] == "launchd"}
    assert groups <= {"tool", "system"}


# ── rules, on synthetic series ──


def _rule(name: str):  # type: ignore[no-untyped-def]
    (r,) = [r for r in rules_for("service") if r.name == name]
    return r


def _s(i: int, status: str = "up", step: int = TWO_MIN, **detail: Any) -> Signal:
    return Signal("service:launchd:x", T0 + i * step, status, detail={"alert": True, **detail})


def test_service_down_on_keepalive_job_closes_when_running_again() -> None:
    series = [_s(0, mode="keepalive"), _s(1, "down", mode="keepalive", last_exit=0),
              _s(2, "down", mode="keepalive", last_exit=0), _s(3, "degraded", mode="keepalive")]
    assert replay(_rule("service.down"), series) == [
        ("open", T0 + 2 * TWO_MIN), ("close", T0 + 3 * TWO_MIN)]


def test_alert_off_never_opens_and_closes_what_was_open() -> None:
    r = _rule("service.down")
    quiet = [_s(i, "down") for i in range(4)]
    for s in quiet:
        s.detail["alert"] = False
    assert replay(r, quiet) == []
    loud_then_quiet = [_s(0, "down"), _s(1, "down"), quiet[2]]
    assert replay(r, loud_then_quiet) == [("open", T0 + TWO_MIN), ("close", T0 + 2 * TWO_MIN)]


def test_port_missing_needs_two_readings_and_ignores_a_dead_process() -> None:
    r = _rule("service.port_missing")
    series = [_s(0, port_ok=True, expected_port=1),
              _s(1, "degraded", port_ok=False, expected_port=1),
              _s(2, "degraded", port_ok=False, expected_port=1),
              _s(3, "down", port_ok=False, expected_port=1),  # process died: service.down's job
              _s(4, port_ok=True, expected_port=1)]
    assert replay(r, series) == [("open", T0 + 2 * TWO_MIN), ("close", T0 + 4 * TWO_MIN)]
    assert replay(r, [_s(0, port_ok=None), _s(1, port_ok=None)]) == []
    assert replay(r, [_s(0, "down", port_ok=False), _s(1, "down", port_ok=False)]) == []


def _h(ok: bool, code: int = 200) -> dict[str, Any]:
    return {"url": "http://127.0.0.1:1/", "code": code, "ms": 3.0, "ok": ok}


def test_health_opens_on_the_second_failure_and_one_success_closes() -> None:
    r = _rule("service.health")
    series = [_s(0, health=_h(True)), _s(1, "degraded", health=_h(False, 503)),
              _s(2, health=_h(True)),  # one failure alone never alerts
              _s(3, "degraded", health=_h(False, 503)), _s(4, "degraded", health=_h(False, 0)),
              _s(5, "degraded", health=_h(False, 0)), _s(6, health=_h(True))]
    assert replay(r, series) == [("open", T0 + 4 * TWO_MIN), ("close", T0 + 6 * TWO_MIN)]
    verdict = r.fn(series[3:5])
    assert verdict.summary == "健康检查连续 2 次失败（连不上）"
    assert verdict.evidence["codes"] == [503, 0]
    # The process being down is service.down's incident, not a second one here.
    assert replay(r, [_s(0, "down", health=_h(False, 0)), _s(1, "down", health=_h(False, 0))]) == []


def test_flapping_counts_launchd_runs_within_ten_minutes() -> None:
    r = _rule("service.flapping")
    runs = [1, 1, 2, 3, 4, 4, 4, 4, 4, 4]  # 3 exits between minutes 2 and 8, then quiet
    series = [_s(i, mode="keepalive", runs=n, pid=100 + n) for i, n in enumerate(runs)]
    events = replay(r, series)
    assert events[0] == ("open", T0 + 4 * TWO_MIN)
    # Quiet from minute 8: closes once the last 10 minutes hold no exit (minute 18).
    assert events[1:] == [("close", T0 + 9 * TWO_MIN)]


def test_flapping_ignores_scheduled_jobs_and_slow_restarts() -> None:
    r = _rule("service.flapping")
    every_minute = [_s(i, "asleep", mode="scheduled", runs=10 + 2 * i) for i in range(8)]
    assert replay(r, every_minute) == []
    # One restart every 6 minutes is never 3 within 10 minutes.
    slow = [_s(i, mode="keepalive", runs=1 + i // 3, step=TWO_MIN) for i in range(15)]
    assert replay(r, slow) == []


def test_exits_between_falls_back_to_pid_changes_and_ignores_reloads() -> None:
    assert exits_between(_s(0, runs=5), _s(1, runs=8)) == 3
    assert exits_between(_s(0, runs=50), _s(1, runs=1)) == 0  # unloaded and loaded again
    assert exits_between(_s(0, pid=1), _s(1, pid=2)) == 1
    assert exits_between(_s(0, pid=1), _s(1, pid=None)) == 1
    assert exits_between(_s(0, pid=1), _s(1, pid=1)) == 0


def test_exposed_reminder_only_for_the_owners_services_and_can_be_ignored() -> None:
    r = _rule("service.exposed")

    def port(i: int, **d: Any) -> Signal:
        return Signal("service:port:3000", T0 + i * TWO_MIN, "up",
                      detail={"exposed": True, "group": "mine", "addresses": ["*"], **d})

    assert replay(r, [port(0), port(1, exposed=False, addresses=["127.0.0.1"])]) == [
        ("open", T0), ("close", T0 + TWO_MIN)]
    assert replay(r, [port(0, group="system"), port(1, group="tool")]) == []
    assert replay(r, [port(0), port(1, exposed_ok=True)]) == [("open", T0), ("close", T0 + TWO_MIN)]
    assert r.fn([port(0, addresses=["*", "127.0.0.1"])]).summary.startswith("监听在 *，")


# ── through the store: the "stop kg-web" story, and gone resources ──


def test_stopping_a_registered_keepalive_job_opens_and_restarting_closes(
        agents: Path, tmp_path: Path) -> None:
    """`launchctl bootout` a registered job: it drops out of `launchctl list`."""
    conn = store.connect(tmp_path / "state.db")
    vault = _vault(tmp_path, REGISTRY)
    m = Machine()
    collector = get_collector("services")
    rid = f"service:launchd:{WEB}"

    def step(i: int) -> list[tuple[str, str, str]]:
        ctx = CollectContext(now=T0 + i * TWO_MIN, vault=vault, run=m.run)
        res = run_collector(conn, collector, ctx=ctx)
        return [(c.action, c.rule, c.resource_id) for c in res.changes if c.resource_id == rid]

    assert step(0) == []
    m.web_loaded = False
    m.listening.pop(8765)
    m.health = "000 0.001"
    assert step(1) == []  # one reading is not an outage
    assert step(2) == [("open", "service.down", rid)]  # within 2 collects (4 min at */2)
    (incident,) = store.list_incidents(conn, open_only=True, resource_id=rid)
    assert incident["summary"] == "登记了，但连续 2 次没加载到 launchd"
    assert incident["severity"] == "critical"
    m.web_loaded, m.runs = True, 1
    m.listening[8765] = ("python3.12", "127.0.0.1", 4243)
    m.health = "200 0.010"
    assert step(3) == [("close", "service.down", rid)]
    assert store.list_incidents(conn, open_only=True, resource_id=rid) == []


def test_a_resource_that_disappears_closes_its_incidents(agents: Path, tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "state.db")
    m = Machine()
    m.listening = {3000: ("node", "*", 7000)}
    collector = get_collector("services")
    first = run_collector(conn, collector, ctx=CollectContext(now=T0, run=m.run))
    assert [(c.action, c.rule) for c in first.changes] == [("open", "service.exposed")]
    m.listening = {}
    second = run_collector(conn, collector, ctx=CollectContext(now=T0 + TWO_MIN, run=m.run))
    assert second.gone == ["service:port:3000"]
    assert [(c.action, c.rule, c.summary) for c in second.changes] == [
        ("close", "service.exposed", "资源已消失")]
    (incident,) = store.list_incidents(conn)
    assert incident["evidence"]["closed"]["reason"] == "gone"


def test_cli_autoloop_receipt_for_a_stopped_service(
        agents: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    from rich.console import Console

    from kg.interfaces.cli.commands.plane import run_plane_collect_command

    m = Machine()
    m.web_loaded = False
    monkeypatch.setattr("kg.plane.collectors.run_command", m.run)
    vault = _vault(tmp_path, REGISTRY)
    console = Console(record=True)
    run_plane_collect_command(name="services", dry_run=False, autoloop=True, console=console,
                              vault=vault)
    assert capsys.readouterr().out.startswith("没事可做：")
    run_plane_collect_command(name="services", dry_run=False, autoloop=True, console=console,
                              vault=vault)
    _, _, receipt = capsys.readouterr().out.partition("\n\n")
    block = receipt.strip().removeprefix("```agent-autoloop").removesuffix("```")
    events = json.loads(block)["events"]
    opened = [e for e in events if e["subject"] == f"service:launchd:{WEB}"]
    assert [e["type"] for e in opened] == ["plane.incident.opened"]
    assert "登记了，但连续 2 次没加载到 launchd" in opened[0]["summary"]


# ── redaction ──

SECRET_LOG = [
    "INFO:     Uvicorn running on http://127.0.0.1:8765 (Press CTRL+C to quit)",
    "export OPENAI_API_KEY=sk-live0123456789abcdefXYZ",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl",
    "login ok password: hunter2-correct-horse",
    "cloned with ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "GET http://127.0.0.1:3350/v1/state?key=s3cr3tvalue 200",
    "fetch https://internal.example.com/api?sig=abc failed",
    "aws AKIAIOSFODNN7EXAMPLE1 configured",
    'client_secret="very secret value" next',
    "上游密钥是 abcdefg12345",
    "blob Zm9vYmFyYmF6cXV4cXV1eGZvb2JhcmJhenF1eHF1dXg9PQ tail",
]
SECRETS = ["sk-live0123456789abcdefXYZ", "eyJhbGciOiJIUzI1NiJ9", "hunter2", "ghp_ABCDEFGHIJ",
           "s3cr3tvalue", "internal.example.com", "AKIAIOSFODNN7", "very secret value",
           "abcdefg12345", "Zm9vYmFyYmF6cXV4"]


def test_redact_masks_every_secret_and_keeps_useful_lines() -> None:
    masked = "\n".join(redact(line) for line in SECRET_LOG)
    for secret in SECRETS:
        assert secret not in masked, secret
    assert redact(SECRET_LOG[0]) == SECRET_LOG[0]  # a loopback URL stays readable
    path = 'File "/Users/someone/Projects/knowledge-graph/agents/api_routes/plane.py", line 3'
    assert redact(path) == path
    assert redact("plain progress line 42%") == "plain progress line 42%"


# ── API ──


@pytest.fixture
def api(agents: Path, tmp_path: Path) -> TestClient:
    from agents.api_routes import services
    from agents.api_routes.plane import plane_db_path

    db = tmp_path / "state.db"
    conn = store.connect(db)
    vault = _vault(tmp_path, REGISTRY + "ignore:\n  exposed: []\n")
    m = Machine()
    m.listening[3000] = ("node", "*", 7000)
    for i in range(2):
        run_collector(conn, get_collector("services"),
                      ctx=CollectContext(now=T0 + i * TWO_MIN, vault=vault, run=m.run))
    conn.close()
    (tmp_path / "logs" / "web.log").write_text(
        "".join(f"line {i}\n" for i in range(30)) + "\n".join(SECRET_LOG) + "\n", encoding="utf-8")
    app = FastAPI()
    app.include_router(services.router, prefix="/api")
    app.dependency_overrides[plane_db_path] = lambda: db
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_api_groups_services_and_nests_ports_under_their_job(api: TestClient) -> None:
    body = api.get("/api/plane/services").json()
    mine = {x["id"]: x for x in body["groups"]["mine"]}
    web = mine[f"service:launchd:{WEB}"]
    assert web["status"] == "up" and web["about"] == "网页" and web["has_log"]
    assert [p["port"] for p in web["ports"]] == [8765]
    assert web["last_restart"] == T0 + TWO_MIN - 3723
    assert "service:port:8765" not in {x["id"] for g in body["groups"].values() for x in g}
    dev = mine["service:port:3000"]
    assert [i["rule"] for i in dev["incidents"]] == ["service.exposed"]
    # Rows with open incidents come first.
    assert body["groups"]["mine"][0]["incidents"]
    assert {x["id"] for x in body["groups"]["tool"]} == {"service:launchd:homebrew.mxcl.db"}
    assert body["incidents"] >= 2  # exposed dev server + the registered job that never existed


def test_api_log_tail_is_redacted_and_only_reads_plist_paths(api: TestClient) -> None:
    rid = f"service:launchd:{WEB}"
    got = api.get(f"/api/plane/services/{rid}/log", params={"lines": 20})
    assert got.status_code == 200, got.text
    body = got.json()
    (f,) = body["files"]
    assert len(f["lines"]) == 20 and body["redacted"]
    text = "\n".join(f["lines"])
    for secret in SECRETS:
        assert secret not in text, secret
    assert "line 29" in text
    helper = api.get("/api/plane/services/service:launchd:com.vendor.helper/log").json()
    assert helper["files"] == []  # no plist, so no log path to read
    assert api.get("/api/plane/services/service:launchd:nope/log").status_code == 404
    assert api.get(f"/api/plane/services/{rid}/log", params={"lines": 999}).status_code == 422
    # DNS rebinding: a request naming another host is refused.
    rebound = api.get(f"/api/plane/services/{rid}/log", headers={"host": "evil.example"})
    assert rebound.status_code == 403


def test_api_without_store_is_empty(tmp_path: Path) -> None:
    from agents.api_routes import services
    from agents.api_routes.plane import plane_db_path

    app = FastAPI()
    app.include_router(services.router, prefix="/api")
    app.dependency_overrides[plane_db_path] = lambda: tmp_path / "missing.db"
    body = TestClient(app).get("/api/plane/services").json()
    assert body["count"] == 0 and body["groups"] == {"mine": [], "tool": [], "system": []}
    assert not (tmp_path / "missing.db").exists()


def test_example_registry_parses_cleanly(tmp_path: Path) -> None:
    import pathlib

    example = pathlib.Path(__file__).resolve().parent.parent / "services.yaml.example"
    reg = load_registry(_vault(tmp_path, example.read_text(encoding="utf-8")))
    assert reg.problems == []
    assert reg.entries["kg-web"].group == "mine" and reg.entries["agent-autoloop"].expect == (401,)
    assert all(is_local_health_url(e.health) for e in reg.entries.values() if e.health)


def test_store_connection_can_be_closed_from_another_thread(tmp_path: Path) -> None:
    """FastAPI runs a sync dependency's setup and teardown on different pool threads."""
    import threading

    conn = store.connect(tmp_path / "state.db")
    errors: list[BaseException] = []

    def close() -> None:
        try:
            conn.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=close)
    t.start()
    t.join()
    assert errors == []
