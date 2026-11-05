"""Behavior of the control plane: ids, the state store, collectors, rules, CLI and API."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rich.console import Console

from kg.plane import store
from kg.plane.collectors import (
    CollectContext,
    Collection,
    CommandError,
    collector_names,
    get_collector,
    run_collector,
)
from kg.plane.collectors.services import (
    LaunchdJob,
    Listener,
    parse_launchctl_list,
    parse_lsof_listen,
    service_down,
)
from kg.plane.ids import is_valid_id, make_id, parse_id
from kg.plane.rules import Rule, Verdict, replay, rules_for
from kg.plane.store import DAY, HOUR, Resource, Signal

if TYPE_CHECKING:
    from pathlib import Path

T0 = 1_762_000_000 - 1_762_000_000 % HOUR  # an hour boundary
FIVE_MIN = 300

LAUNCHCTL = """PID\tStatus\tLabel
-\t0\tcom.apple.SafariHistoryServiceAgent
4242\t0\tcom.example.web
-\t0\tcom.example.nightly-report
-\t78\tcom.example.broken
-\t-15\tcom.example.quit-by-user
5151\t1\tcom.example.restarted
13170\t0\tapplication.com.example.Editor.1152921500311901956.1152921500311901961
"""

LSOF = """p4242
cpython3.12
f7
n127.0.0.1:8765
p6001
cnode
f20
n*:3000
f21
n[::1]:3000
p6002
cSome Helper
f9
n[::1]:42050
"""


def _db(tmp_path: Path) -> sqlite3.Connection:
    return store.connect(tmp_path / "vault" / ".kg" / "plane" / "state.db")


# ── ids ──


def test_make_id_joins_kind_and_parts_and_round_trips() -> None:
    rid = make_id("service", "launchd", "com.example.web")
    assert rid == "service:launchd:com.example.web"
    assert parse_id(rid) == ("service", ("launchd", "com.example.web"))
    assert make_id("egress", "线路A") == "egress:线路A"  # non-ASCII is kept verbatim


@pytest.mark.parametrize("args", [
    ("server", "x"),  # unknown kind
    ("service",),  # no local id
    ("service", "launchd", "has space"),
    ("service", "launchd", "a:b"),
    ("service", ""),
    ("service", "x" * 300),
])
def test_make_id_refuses_unstable_ids(args: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        make_id(*args)


def test_is_valid_id() -> None:
    assert is_valid_id("session:claude:7f3a9c1e-0000")
    assert not is_valid_id("service")
    assert not is_valid_id("Service:x")
    assert not is_valid_id("service::x")


# ── store ──


def test_connect_migrates_once_and_uses_wal(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"resource", "signal", "incident"} <= tables
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"signal_resource_ts", "incident_open_once"} <= indexes
    conn.close()
    again = _db(tmp_path)  # reopening does not re-run the migration
    assert again.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION


def test_connect_refuses_a_newer_store(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    conn.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(RuntimeError, match="比代码认识的"):
        _db(tmp_path)


def test_upsert_tracks_new_and_returning_and_mark_gone(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    a = Resource("service:port:1", "service", "a", "services", {"port": 1})
    b = Resource("service:port:2", "service", "b", "services")
    first = store.upsert_resources(conn, [a, b], now=T0)
    assert first.new == ["service:port:1", "service:port:2"] and first.returned == []

    assert store.mark_gone(conn, "services", ["service:port:1"], now=T0 + 60) == ["service:port:2"]
    assert store.get_resource(conn, "service:port:2")["gone_at"] == T0 + 60
    assert [r["id"] for r in store.list_resources(conn)] == ["service:port:1"]

    again = store.upsert_resources(conn, [b], now=T0 + 120)
    assert again.new == [] and again.returned == ["service:port:2"]
    row = store.get_resource(conn, "service:port:2")
    assert row["gone_at"] is None and row["first_seen"] == T0 and row["last_seen"] == T0 + 120
    assert store.get_resource(conn, "service:port:1")["attrs"] == {"port": 1}


def test_mark_gone_only_inside_covered_prefixes(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    store.upsert_resources(conn, [Resource("service:port:1", "service", "", "services"),
                                  Resource("service:launchd:x", "service", "", "services")], now=T0)
    gone = store.mark_gone(conn, "services", [], now=T0 + 1, prefixes=["service:launchd:"])
    assert gone == ["service:launchd:x"]
    assert store.mark_gone(conn, "other", [], now=T0 + 1) == []  # another source's resources


def test_upsert_rejects_kind_that_disagrees_with_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不一致"):
        store.upsert_resources(_db(tmp_path), [Resource("service:port:1", "egress", "", "x")])


def test_signals_round_trip_and_latest_status(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    store.upsert_resources(conn, [Resource("egress:line", "egress", "line", "egress")], now=T0)
    store.append_signals(conn, [
        Signal("egress:line", T0, "up", 462.0, "ms", {"exit": 0}),
        Signal("egress:line", T0 + FIVE_MIN, "down", None, None, {"exit": 35}),
    ])
    got = store.list_signals(conn, "egress:line")
    assert [s.status for s in got] == ["up", "down"] and got[0].detail == {"exit": 0}
    assert [s.ts for s in store.list_signals(conn, "egress:line", limit=1)] == [T0 + FIVE_MIN]
    assert store.list_signals(conn, "egress:line", since=T0 + 1) == got[1:]
    (row,) = store.list_resources(conn, kind="egress")
    assert row["status"] == "down" and row["status_ts"] == T0 + FIVE_MIN
    with pytest.raises(ValueError):
        store.append_signals(conn, [Signal("egress:line", T0, "broken")])


def test_compact_folds_old_hours_and_drops_after_a_year(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    rid = "egress:line"
    now = T0 + 40 * DAY
    old_hour = T0  # 40 days ago: folded
    raw = [Signal(rid, old_hour + i * FIVE_MIN, "up", 100.0 + i, "ms") for i in range(10)]
    raw += [Signal(rid, old_hour + 50 * 60, "down"), Signal(rid, old_hour + 55 * 60, "down")]
    recent = Signal(rid, now - DAY, "up", 1.0, "ms")  # inside 30 days: kept raw
    ancient = Signal(rid, now - 400 * DAY, "up")  # beyond a year: dropped
    store.append_signals(conn, [*raw, recent, ancient])

    result = store.compact(conn, now=now)
    assert (result.folded, result.buckets, result.dropped) == (12, 1, 1)
    rows = store.list_signals(conn, rid)
    assert len(rows) == 2
    rollup, kept = rows
    assert kept == recent
    assert rollup.ts == old_hour and rollup.samples == 12 and rollup.status == "up"
    assert rollup.detail == {"statuses": {"down": 2, "up": 10}, "min": 100.0, "max": 109.0}
    assert rollup.value == pytest.approx(104.5) and rollup.unit == "ms"

    # Idempotent: nothing more to fold until another hour crosses the cutoff.
    assert store.compact(conn, now=now) == store.CompactResult()
    # A late raw reading for an already folded hour is merged into its rollup.
    store.append_signals(conn, [Signal(rid, old_hour + 1, "down")])
    store.compact(conn, now=now)
    (rollup2, _) = store.list_signals(conn, rid)
    assert rollup2.samples == 13 and rollup2.detail["statuses"] == {"down": 3, "up": 10}


def test_rollup_ties_go_to_the_worse_status(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    store.append_signals(conn, [Signal("service:x", T0, "up"),
                                Signal("service:x", T0 + 60, "down")])
    store.compact(conn, now=T0 + 31 * DAY)
    (rollup,) = store.list_signals(conn, "service:x")
    assert rollup.status == "down" and rollup.value is None


def test_incident_open_is_idempotent_and_close_merges_evidence(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    iid = store.open_incident(conn, resource_id="service:x", rule="service.down", opened_at=T0,
                              severity="critical", summary="down", evidence={"since": T0})
    assert iid is not None
    assert store.open_incident(conn, resource_id="service:x", rule="service.down",
                               opened_at=T0 + 1) is None
    with pytest.raises(sqlite3.IntegrityError):  # the index enforces it even past the check
        conn.execute("INSERT INTO incident(resource_id, rule, opened_at, severity)"
                     " VALUES ('service:x', 'service.down', 1, 'info')")
    assert store.close_incident(conn, resource_id="service:x", rule="service.down",
                                closed_at=T0 + 600, evidence={"ts": T0 + 600}) == iid
    assert store.close_incident(conn, resource_id="service:x", rule="service.down",
                                closed_at=T0 + 700) is None
    (row,) = store.list_incidents(conn)
    assert row["closed_at"] == T0 + 600
    assert row["evidence"] == {"since": T0, "closed": {"ts": T0 + 600}}
    assert store.list_incidents(conn, open_only=True) == []
    # A new outage after closing opens a new incident.
    assert store.open_incident(conn, resource_id="service:x", rule="service.down",
                               opened_at=T0 + 900) not in (None, iid)


def test_transaction_rolls_back_on_error_and_on_request(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    with store.transaction(conn, rollback=True):
        store.append_signals(conn, [Signal("service:x", T0, "up")])
    with pytest.raises(ZeroDivisionError), store.transaction(conn):
        store.append_signals(conn, [Signal("service:x", T0, "up")])
        1 / 0  # noqa: B018
    assert store.list_signals(conn, "service:x") == []


# ── rules ──


def _series(statuses: str, start: int = T0) -> list[Signal]:
    """'uudd' -> up, up, down, down every five minutes; 's' = asleep, '?' = unknown."""
    names = {"u": "up", "d": "down", "s": "asleep", "?": "unknown"}
    return [Signal("service:launchd:x", start + i * FIVE_MIN, names[c])
            for i, c in enumerate(statuses)]


def _service_rule() -> Rule:
    (r,) = [r for r in rules_for("service") if r.name == "service.down"]
    return r


def test_service_down_opens_after_two_downs_and_closes_on_first_up() -> None:
    r = _service_rule()
    #            0123456789
    series = _series("uudsddduud")
    # One down (2), then asleep (3) breaks the run; downs at 4,5 open at 5; up at 7 closes;
    # the lone down at 9 does not reopen.
    assert replay(r, series) == [("open", T0 + 5 * FIVE_MIN), ("close", T0 + 7 * FIVE_MIN)]


def test_service_down_ignores_asleep_and_unknown_and_flapping() -> None:
    r = _service_rule()
    assert replay(r, _series("sssss?????")) == []
    assert replay(r, _series("dudududu")) == []
    # Asleep or unknown while open keeps it open; only an up closes.
    assert replay(r, _series("dds??dsu")) == [("open", T0 + FIVE_MIN), ("close", T0 + 7 * FIVE_MIN)]


def test_service_down_verdict_carries_evidence() -> None:
    history = [Signal("service:launchd:x", T0, "down", detail={"last_exit": 78}),
               Signal("service:launchd:x", T0 + 60, "down", detail={"last_exit": 78})]
    verdict = service_down(history)
    assert verdict is not None and verdict.firing and verdict.severity == "critical"
    assert verdict.evidence == {"since": T0, "ts": T0 + 60, "last_exit": [78, 78]}


# ── collector framework ──


class _Scripted:
    """A collector that reports a prepared status per resource each run."""

    name = "scripted"

    def __init__(self) -> None:
        self.statuses: dict[str, str] = {}
        self.scopes: tuple[str, ...] | None = None

    def collect(self, ctx: CollectContext) -> Collection:
        return Collection(
            resources=[Resource(rid, "service", rid, "ignored") for rid in self.statuses],
            signals=[Signal(rid, ctx.now, st) for rid, st in self.statuses.items()],
            scopes=self.scopes,
        )


def test_run_collector_writes_marks_gone_and_drives_incidents(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    c = _Scripted()
    timeline = [({"service:launchd:a": "up", "service:launchd:b": "up"}, [], []),
                ({"service:launchd:a": "down", "service:launchd:b": "up"}, [], []),
                ({"service:launchd:a": "down"}, ["open"], ["service:launchd:b"]),
                ({"service:launchd:a": "down", "service:launchd:b": "up"}, [], []),
                ({"service:launchd:a": "up", "service:launchd:b": "up"}, ["close"], [])]
    for step, (statuses, actions, gone) in enumerate(timeline):
        c.statuses = statuses
        result = run_collector(conn, c, ctx=CollectContext(now=T0 + step * FIVE_MIN))
        assert [ch.action for ch in result.changes] == actions, step
        assert result.gone == gone, step
    assert store.get_resource(conn, "service:launchd:a")["source"] == "scripted"
    assert store.get_resource(conn, "service:launchd:b")["gone_at"] is None  # came back at step 3
    (incident,) = store.list_incidents(conn)
    assert incident["opened_at"] == T0 + 2 * FIVE_MIN and incident["closed_at"] == T0 + 4 * FIVE_MIN
    assert incident["rule"] == "service.down" and incident["severity"] == "critical"
    assert len(store.list_signals(conn, "service:launchd:a")) == 5


def test_run_collector_dry_run_writes_nothing(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    c = _Scripted()
    c.statuses = {"service:launchd:a": "down"}
    run_collector(conn, c, ctx=CollectContext(now=T0))
    result = run_collector(conn, c, ctx=CollectContext(now=T0 + FIVE_MIN), dry_run=True)
    assert [ch.action for ch in result.changes] == ["open"]  # what would happen
    assert store.list_incidents(conn) == []
    assert len(store.list_signals(conn, "service:launchd:a")) == 1


def test_run_collector_rejects_signals_for_unreported_resources(tmp_path: Path) -> None:
    class Bad:
        name = "bad"

        def collect(self, ctx: CollectContext) -> Collection:
            return Collection(signals=[Signal("service:x", ctx.now, "up")])

    with pytest.raises(ValueError, match="没上报"):
        run_collector(_db(tmp_path), Bad(), ctx=CollectContext(now=T0))


def test_registry_discovers_the_services_collector() -> None:
    assert "services" in collector_names()
    assert get_collector("services").name == "services"
    with pytest.raises(KeyError):
        get_collector("nope")


def test_rule_registration_rejects_a_second_rule_with_the_same_name() -> None:
    from kg.plane.rules import rule

    with pytest.raises(ValueError, match="已被"):
        rule("service", "service.down", window=1)(lambda history: Verdict(False))


# ── services collector ──


def test_parse_launchctl_list() -> None:
    jobs = parse_launchctl_list(LAUNCHCTL)
    assert jobs[1] == LaunchdJob("com.example.web", 4242, 0)
    assert jobs[3] == LaunchdJob("com.example.broken", None, 78)
    assert len(jobs) == 7


def test_parse_lsof_listen() -> None:
    assert parse_lsof_listen(LSOF) == [
        Listener(8765, 4242, "python3.12", "127.0.0.1"),
        Listener(3000, 6001, "node", "*"),
        Listener(3000, 6001, "node", "[::1]"),
        Listener(42050, 6002, "Some Helper", "[::1]"),
    ]


def _fake_run(outputs: dict[str, str | Exception]):  # type: ignore[no-untyped-def]
    def run(argv: list[str], timeout: float = 20) -> str:
        out = outputs[argv[0]]
        if isinstance(out, Exception):
            raise out
        return out
    return run


def test_services_collector_maps_jobs_and_ports(tmp_path: Path) -> None:
    ctx = CollectContext(now=T0, run=_fake_run({"launchctl": LAUNCHCTL, "lsof": LSOF}))
    out = get_collector("services").collect(ctx)
    status = {s.resource_id: s.status for s in out.signals}
    assert status == {
        "service:launchd:com.example.web": "up",
        "service:launchd:com.example.nightly-report": "asleep",
        "service:launchd:com.example.broken": "down",
        "service:launchd:com.example.quit-by-user": "unknown",
        "service:launchd:com.example.restarted": "up",
        "service:port:3000": "up",
        "service:port:8765": "up",
        "service:port:42050": "up",
    }
    ports = {r.id: r.attrs for r in out.resources if r.attrs["manager"] == "port"}
    assert ports["service:port:3000"]["exposed"] is True
    assert ports["service:port:8765"]["exposed"] is False
    assert ports["service:port:3000"]["addresses"] == ["*", "[::1]"]
    assert out.scopes == ("service:launchd:", "service:port:")
    assert all(is_valid_id(r.id) for r in out.resources)


def test_services_collector_honours_local_ignore_list(tmp_path: Path) -> None:
    config = tmp_path / ".kg" / "plane" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("services:\n  ignore_prefixes: [com.example.n]\n", encoding="utf-8")
    ctx = CollectContext(now=T0, vault=tmp_path,
                         run=_fake_run({"launchctl": LAUNCHCTL, "lsof": ""}))
    ids = {r.id for r in get_collector("services").collect(ctx).resources}
    assert "service:launchd:com.example.nightly-report" not in ids
    assert "service:launchd:com.example.web" in ids


def test_services_collector_failing_lsof_keeps_ports_alive(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    collector = get_collector("services")
    ok = CollectContext(now=T0, run=_fake_run({"launchctl": LAUNCHCTL, "lsof": LSOF}))
    run_collector(conn, collector, ctx=ok)
    broken = CollectContext(now=T0 + FIVE_MIN,
                            run=_fake_run({"launchctl": LAUNCHCTL,
                                           "lsof": CommandError("lsof: boom")}))
    result = run_collector(conn, collector, ctx=broken)
    assert result.gone == []
    assert any("lsof" in n for n in result.notes)
    assert store.get_resource(conn, "service:port:8765")["gone_at"] is None
    # When lsof works again and a port is missing, that port goes.
    fewer = LSOF.split("p6001")[0]
    later = CollectContext(now=T0 + 2 * FIVE_MIN,
                           run=_fake_run({"launchctl": LAUNCHCTL, "lsof": fewer}))
    gone = run_collector(conn, collector, ctx=later).gone
    assert gone == ["service:port:3000", "service:port:42050"]


def test_broken_job_opens_an_incident_on_the_second_collect(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    collector = get_collector("services")
    run = _fake_run({"launchctl": LAUNCHCTL, "lsof": LSOF})
    first = run_collector(conn, collector, ctx=CollectContext(now=T0, run=run))
    second = run_collector(conn, collector, ctx=CollectContext(now=T0 + FIVE_MIN, run=run))
    assert first.changes == []
    assert [(c.action, c.resource_id) for c in second.changes] == [
        ("open", "service:launchd:com.example.broken")]


# ── CLI ──


def _patch_services(monkeypatch: pytest.MonkeyPatch, launchctl: str) -> None:
    monkeypatch.setattr("kg.plane.collectors.run_command",
                        _fake_run({"launchctl": launchctl, "lsof": LSOF}))


def test_cli_collect_autoloop_is_quiet_until_an_incident_opens(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    from kg.interfaces.cli.commands.plane import run_plane_collect_command

    _patch_services(monkeypatch, LAUNCHCTL)
    console = Console(record=True)
    run_plane_collect_command(name="services", dry_run=False, autoloop=True, console=console,
                              vault=tmp_path)
    first = capsys.readouterr().out
    assert first.startswith("没事可做：services：8 个资源")
    assert "agent-autoloop" not in first

    run_plane_collect_command(name="services", dry_run=False, autoloop=True, console=console,
                              vault=tmp_path)
    second = capsys.readouterr().out
    line, _, receipt = second.partition("\n\n")
    assert "事故 新开 1、关闭 0" in line
    payload = json.loads(receipt.strip().removeprefix("```agent-autoloop").removesuffix("```"))
    (event,) = payload["events"]
    assert event["type"] == "plane.incident.opened"
    assert event["subject"] == "service:launchd:com.example.broken"
    assert event["id"].startswith("plane-incident-") and event["id"].endswith("-opened")


def test_cli_dry_run_does_not_create_the_store(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kg.interfaces.cli.commands.plane import run_plane_collect_command, run_plane_status_command

    _patch_services(monkeypatch, LAUNCHCTL)
    console = Console(record=True, width=200)
    run_plane_collect_command(name="services", dry_run=True, autoloop=False, console=console,
                              vault=tmp_path)
    assert "预览，不写入" in console.export_text()
    assert not store.db_path(tmp_path).exists()
    run_plane_status_command(console=console, vault=tmp_path)
    assert "状态库还没建" in console.export_text()


def test_cli_status_summarises_the_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kg.interfaces.cli.commands.plane import run_plane_collect_command, run_plane_status_command

    _patch_services(monkeypatch, LAUNCHCTL)
    quiet = Console(record=True)
    for _ in range(2):
        run_plane_collect_command(name="services", dry_run=False, autoloop=False, console=quiet,
                                  vault=tmp_path)
    console = Console(record=True, width=200)
    run_plane_status_command(console=console, vault=tmp_path)
    text = console.export_text()
    assert "8 个资源在线" in text
    assert "services：8 个，asleep 1，down 1，unknown 1，up 5" in text
    assert "[critical] service:launchd:com.example.broken" in text  # not eaten as markup
    assert "未关闭的事故：1" in text and "service:launchd:com.example.broken" in text


def test_cli_unknown_collector_exits(tmp_path: Path) -> None:
    from kg.interfaces.cli.commands.plane import run_plane_collect_command

    with pytest.raises(SystemExit):
        run_plane_collect_command(name="nope", dry_run=False, autoloop=False,
                                  console=Console(record=True), vault=tmp_path)


# ── API ──


@pytest.fixture
def api(tmp_path: Path) -> tuple[TestClient, sqlite3.Connection]:
    from agents.api_routes import plane

    path = tmp_path / "state.db"
    conn = store.connect(path)
    app = FastAPI()
    app.include_router(plane.router, prefix="/api")
    app.dependency_overrides[plane.plane_db_path] = lambda: path
    return TestClient(app), conn


def _seed(conn: sqlite3.Connection) -> None:
    store.upsert_resources(conn, [Resource("service:launchd:a", "service", "a", "services"),
                                  Resource("egress:line", "egress", "line", "egress"),
                                  Resource("service:port:1", "service", "p", "services")], now=T0)
    store.mark_gone(conn, "services", ["service:launchd:a"], now=T0 + 1)
    store.append_signals(conn, [Signal("egress:line", T0 + i * FIVE_MIN, "up", 400.0 + i, "ms")
                                for i in range(5)])
    store.open_incident(conn, resource_id="egress:line", rule="egress.down", opened_at=T0)
    store.open_incident(conn, resource_id="service:launchd:a", rule="service.down", opened_at=T0)
    store.close_incident(conn, resource_id="service:launchd:a", rule="service.down",
                         closed_at=T0 + 60)


def test_api_resources(api: tuple[TestClient, sqlite3.Connection]) -> None:
    client, conn = api
    _seed(conn)
    body = client.get("/api/plane/resources").json()
    assert [r["id"] for r in body["items"]] == ["egress:line", "service:launchd:a"]
    assert body["items"][0]["status"] == "up" and body["items"][0]["value"] == 404.0
    services = client.get("/api/plane/resources", params={"kind": "service", "gone": 1}).json()
    assert {r["id"] for r in services["items"]} == {"service:launchd:a", "service:port:1"}
    assert client.get("/api/plane/resources/service:port:1").json()["gone_at"] == T0 + 1
    assert client.get("/api/plane/resources/service:port:9").status_code == 404


def test_api_signals(api: tuple[TestClient, sqlite3.Connection]) -> None:
    client, conn = api
    _seed(conn)
    body = client.get("/api/plane/signals",
                      params={"resource": "egress:line", "since": T0 + 2 * FIVE_MIN}).json()
    assert [s["ts"] for s in body["items"]] == [T0 + i * FIVE_MIN for i in (2, 3, 4)]
    assert body["items"][0]["unit"] == "ms"
    limited = client.get("/api/plane/signals",
                         params={"resource": "egress:line", "since": 0, "limit": 2}).json()
    assert [s["ts"] for s in limited["items"]] == [T0 + 3 * FIVE_MIN, T0 + 4 * FIVE_MIN]
    assert client.get("/api/plane/signals").status_code == 422  # resource is required


def test_api_incidents(api: tuple[TestClient, sqlite3.Connection]) -> None:
    client, conn = api
    _seed(conn)
    assert client.get("/api/plane/incidents").json()["count"] == 2
    (only,) = client.get("/api/plane/incidents", params={"open": 1}).json()["items"]
    assert only["resource_id"] == "egress:line" and only["closed_at"] is None
    by_resource = client.get("/api/plane/incidents", params={"resource": "service:launchd:a"})
    assert by_resource.json()["items"][0]["closed_at"] == T0 + 60


def test_api_without_a_store_is_empty_and_creates_nothing(tmp_path: Path) -> None:
    from agents.api_routes import plane

    path = tmp_path / "missing" / "state.db"
    app = FastAPI()
    app.include_router(plane.router, prefix="/api")
    app.dependency_overrides[plane.plane_db_path] = lambda: path
    client = TestClient(app)
    assert client.get("/api/plane/resources").json() == {"items": [], "count": 0}
    assert client.get("/api/plane/incidents", params={"open": 1}).json()["count"] == 0
    assert not path.parent.exists()


def test_kg_serve_listens_on_loopback_by_default() -> None:
    import inspect

    from kg.interfaces.cli.commands.serve import run_serve_command

    assert inspect.signature(run_serve_command).parameters["host"].default == "127.0.0.1"
