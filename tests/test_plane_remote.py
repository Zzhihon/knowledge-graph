"""Remote services over SSH: the generated script runs under a real /bin/sh against a fake home.

`ssh` is replaced by running the remote command line locally with HOME pointed at a tmp dir and
a fake `launchctl` first on PATH; stat, tail, date and /usr/bin/sqlite3 are the real ones, so
quoting, the section format and the fares SQL are exercised as on the remote Mac.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from kg.plane import store
from kg.plane.collectors import CollectContext, CommandError, run_collector
from kg.plane.collectors.remote import (
    RemoteCollector,
    fares_block,
    load_config,
    parse_output,
    remote_push_failing,
)
from kg.plane.collectors.services import exits_between
from kg.plane.rules import replay, rules_for
from kg.plane.store import Signal

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(not os.path.exists("/usr/bin/sqlite3"),
                                reason="needs /usr/bin/sqlite3 (macOS)")

RID = "service:remote:air:local.ticket-monitor"
TUNNEL = "service:remote:air:local.tg-tunnel"
CONFIG = """hosts:
  air:
    ssh: mac-air
    services:
      local.ticket-monitor:
        about: 机票监控
        dir: ~/apps/tm
        heartbeat: {path: data/.heartbeat, max_age_s: 180}
        push: {ledger: logs/push_history.jsonl, channels: [telegram], fail_after: 2}
        fares_db: data/prices.db
      local.tg-tunnel:
        about: 隧道
"""
PRINT = """gui/501/{label} = {{
\tactive count = 1
\tstate = {state}
\truns = {runs}
{pid}\tlast exit code = {code}
\tendpoints = {{
\t\t"x" = {{
\t\t\tstate = active
\t\t}}
\t}}
}}
"""


class Remote:
    """A fake remote Mac: a home dir, launchd jobs, and `ssh` that runs the script locally."""

    def __init__(self, tmp: Path) -> None:
        self.home = tmp / "home"
        self.app = self.home / "apps" / "tm"
        (self.app / "data").mkdir(parents=True)
        (self.app / "logs").mkdir()
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.jobs: dict[str, dict] = {}
        self.up = True
        self.calls: list[list[str]] = []

    def job(self, label: str, *, pid: int | None = 4242, runs: int = 1, code: str = "0") -> None:
        self.jobs[label] = {"pid": pid, "runs": runs, "code": code}

    def heartbeat(self, age: int) -> None:
        f = self.app / "data" / ".heartbeat"
        f.write_text("{}")
        t = time.time() - age
        os.utime(f, (t, t))

    def push(self, *records: tuple[str, bool]) -> None:
        with (self.app / "logs" / "push_history.jsonl").open("a") as f:
            for ch, ok in records:
                f.write(json.dumps({"ts": "2026-11-15 16:00:00", "ok": ok, "ch": ch,
                                    "title": "t", "desp": "secret body"}) + "\n")

    def _launchctl(self) -> None:
        lines = ["#!/bin/sh", 'case "$2" in']
        for label, j in self.jobs.items():
            pid = f"\tpid = {j['pid']}\n" if j["pid"] else ""
            text = PRINT.format(label=label, state="running" if j["pid"] else "not running",
                                runs=j["runs"], pid=pid, code=j["code"])
            lines.append(f"  */{label}) cat <<'EOF'\n{text}EOF\n  ;;")
        lines += ["  *) echo 'Could not find service' >&2; exit 113;;", "esac"]
        f = self.bin / "launchctl"
        f.write_text("\n".join(lines) + "\n")
        f.chmod(0o755)

    def run(self, argv: list[str], timeout: float = 20) -> str:
        self.calls.append(argv)
        assert argv[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
        if not self.up:
            raise CommandError("ssh 退出码 255: ssh: connect to host mac-air port 22: timed out")
        self._launchctl()
        env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin"}
        done = subprocess.run(["/bin/sh", "-c", argv[-1]], capture_output=True, text=True,
                              env=env, timeout=timeout, check=False)
        return done.stdout


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    folder = tmp_path / "vault" / ".kg" / "plane"
    folder.mkdir(parents=True)
    (folder / "remote.yaml").write_text(CONFIG, encoding="utf-8")
    return tmp_path / "vault"


@pytest.fixture
def remote(tmp_path: Path) -> Remote:
    r = Remote(tmp_path)
    r.job("local.ticket-monitor")
    r.job("local.tg-tunnel", pid=5151)
    r.heartbeat(10)
    return r


def _collect(vault: Path, remote: Remote, now: int | None = None):
    ctx = CollectContext(now=now or int(time.time()), vault=vault, run=remote.run)
    return RemoteCollector().collect(ctx)


def _by_id(collection) -> dict[str, Signal]:
    return {s.resource_id: s for s in collection.signals}


def test_config_resolves_paths_and_ids(vault: Path) -> None:
    cfg = load_config(vault)
    (host,) = cfg.hosts
    assert host.ssh == "mac-air" and cfg.problems == []
    tm = host.services[0]
    assert tm.heartbeat == "~/apps/tm/data/.heartbeat"
    assert tm.push_channels == ("telegram",) and tm.fares_db == "~/apps/tm/data/prices.db"


def test_healthy_host_is_up(vault: Path, remote: Remote) -> None:
    out = _collect(vault, remote)
    sig = _by_id(out)
    assert {r.id for r in out.resources} == {RID, TUNNEL}
    assert sig[RID].status == "up" and sig[TUNNEL].status == "up"
    d = sig[RID].detail
    assert d["pid"] == 4242 and d["runs"] == 1 and d["state"] == "running"
    assert 0 <= d["heartbeat_age_s"] < 60 and sig[RID].unit == "s"
    assert d["push"] == {"last": None, "failing": False, "fails": []}
    assert d["fares"] == [] and "fares_error" not in d  # no db yet
    assert len(remote.calls) == 1  # one ssh per host


def test_stale_heartbeat_and_stopped_job_are_down(vault: Path, remote: Remote) -> None:
    remote.heartbeat(600)
    remote.job("local.tg-tunnel", pid=None, code="-15")
    sig = _by_id(_collect(vault, remote))
    assert sig[RID].status == "down" and sig[RID].detail["reason"] == "heartbeat_stale"
    assert sig[TUNNEL].status == "down" and sig[TUNNEL].detail["reason"] == "not_running"
    assert sig[TUNNEL].detail["last_exit"] == -15


def test_unloaded_job_is_down(vault: Path, remote: Remote) -> None:
    del remote.jobs["local.tg-tunnel"]
    sig = _by_id(_collect(vault, remote))
    assert sig[TUNNEL].status == "down" and sig[TUNNEL].detail["reason"] == "not_loaded"


def test_failing_pushes_degrade_and_only_watched_channel_counts(vault: Path,
                                                               remote: Remote) -> None:
    remote.push(("telegram", False), ("dingtalk", True), ("telegram", False))
    sig = _by_id(_collect(vault, remote))
    assert sig[RID].status == "degraded"
    push = sig[RID].detail["push"]
    assert push["failing"] and len(push["fails"]) == 2
    assert "secret body" not in json.dumps(sig[RID].detail, ensure_ascii=False)
    remote.push(("telegram", True))
    assert _by_id(_collect(vault, remote))[RID].status == "up"


def test_unreachable_host_is_unknown(vault: Path, remote: Remote) -> None:
    remote.up = False
    out = _collect(vault, remote)
    sig = _by_id(out)
    assert {s.status for s in sig.values()} == {"unknown"}
    assert sig[RID].detail["reachable"] is False and out.notes


def test_fares_lowest_per_route_and_date(vault: Path, remote: Remote) -> None:
    db = remote.app / "data" / "prices.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE flight_prices (id INTEGER PRIMARY KEY, platform TEXT NOT NULL,
        from_city TEXT NOT NULL, to_city TEXT NOT NULL, depart_date TEXT NOT NULL,
        price REAL NOT NULL, airline TEXT, flight_no TEXT, depart_time TEXT, arrive_time TEXT,
        fetched_at TEXT NOT NULL, extra TEXT, prov TEXT NOT NULL DEFAULT '')""")
    now = datetime.now()
    fresh = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    old = (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    future = (now + timedelta(days=30)).strftime("%Y-%m-%d")
    past = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = [("ctrip", "HKG", "NRT", future, 2100, "CX", "CX500", fresh),
            ("tongcheng", "HKG", "NRT", future, 1890, "UO", "UO846", fresh),
            ("ctrip", "HKG", "NRT", future, 900, "XX", "OLD1", old),  # fetched too long ago
            ("ctrip", "HKG", "HND", past, 800, "XX", "PAST", fresh),  # departure passed
            ("ctrip", "HKG", "HND", future, 2500, "NH", "NH860", fresh)]
    conn.executemany("INSERT INTO flight_prices (platform, from_city, to_city, depart_date, "
                     "price, airline, flight_no, fetched_at) VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    fares = _by_id(_collect(vault, remote))[RID].detail["fares"]
    assert [(f["to_city"], f["price"], f["flight_no"], f["platform"]) for f in fares] == [
        ("HND", 2500, "NH860", "ctrip"), ("NRT", 1890, "UO846", "tongcheng")]


def test_fares_split_one_way_and_round_trip(vault: Path, remote: Remote) -> None:
    db = remote.app / "data" / "prices.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE flight_prices (id INTEGER PRIMARY KEY, platform TEXT NOT NULL,
        from_city TEXT NOT NULL, to_city TEXT NOT NULL, depart_date TEXT NOT NULL,
        price REAL NOT NULL, airline TEXT, flight_no TEXT, depart_time TEXT, arrive_time TEXT,
        fetched_at TEXT NOT NULL, extra TEXT, prov TEXT NOT NULL DEFAULT '')""")
    fresh = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    dep = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    ret = (datetime.now() + timedelta(days=37)).strftime("%Y-%m-%d")
    rt = json.dumps([{"tripType": "RT", "backDate": ret}])
    rows = [("ctrip", 805, "CX500", None), ("ctrip", 2900, "CX504", rt),
            ("tongcheng", 2650, "UO850", rt), ("ctrip", 990, "HX608", "not json")]
    conn.executemany("INSERT INTO flight_prices (platform, from_city, to_city, depart_date, "
                     "price, flight_no, fetched_at, extra) VALUES (?,'HKG','NRT',?,?,?,?,?)",
                     [(p, dep, pr, f, fresh, ex) for p, pr, f, ex in rows])
    conn.commit()
    conn.close()
    fares = _by_id(_collect(vault, remote))[RID].detail["fares"]
    assert [(f["ret_date"], f["price"], f["flight_no"]) for f in fares] == [
        (None, 805, "CX500"), (ret, 2650, "UO850")]


def test_routes_bring_only_routes_and_thresholds(vault: Path, remote: Remote) -> None:
    cfg = vault / ".kg" / "plane" / "remote.yaml"
    cfg.write_text(CONFIG.replace(
        "        fares_db: data/prices.db\n",
        "        fares_db: data/prices.db\n"
        f"        routes: {{config: config.yaml, python: {sys.executable}}}\n"), encoding="utf-8")
    (remote.app / "config.yaml").write_text("""users:
- name: hzh
  routes:
  - {from: HKG, to: NRT, from_name: 中国香港, to_name: 东京成田, dates: ['2027-03-28'],
     ret_dates: ['2027-04-04'], alert_direct: 3500}
  - {from: HKG, to: HND, dates: ['2027-03-28']}
  notifier: {telegram: {bot_token: SECRET-TOKEN, llm: [{api_key: SECRET-KEY}]}}
""", encoding="utf-8")
    sig = _by_id(_collect(vault, remote))[RID]
    assert sig.detail["routes"] == [
        {"from": "HKG", "to": "NRT", "from_name": "中国香港", "to_name": "东京成田",
         "dates": ["2027-03-28"], "ret_dates": ["2027-04-04"], "budget": 3500.0},
        {"from": "HKG", "to": "HND", "from_name": "HKG", "to_name": "HND",
         "dates": ["2027-03-28"], "ret_dates": [], "budget": 0.0}]
    assert "SECRET" not in json.dumps(sig.detail)


def test_cut_short_output_is_an_error() -> None:
    with pytest.raises(CommandError):
        parse_output("@@now 1\n@@launchd x\n\tstate = running\n")
    now, out = parse_output("@@now 7\n@@launchd x\n\tpid = 1\n@@end -\n")
    assert now == 7 and out == {"x": {"launchd": "pid = 1"}}


# ── rules and the store ──


def test_down_opens_after_two_and_sleep_neither_opens_nor_counts_as_restart(
        tmp_path: Path, vault: Path, remote: Remote) -> None:
    conn = store.connect(tmp_path / "state.db")
    collector = RemoteCollector()
    t = int(time.time())

    def step(i: int):
        return run_collector(conn, collector,
                             ctx=CollectContext(now=t + i * 120, vault=vault, run=remote.run))

    step(0)
    remote.up = False
    for i in range(1, 4):  # the laptop sleeps for three rounds
        assert step(i).changes == []
    remote.up = True
    remote.heartbeat(900)
    assert step(4).changes == []  # first down
    opened = step(5).changes
    assert [(c.action, c.rule, c.resource_id) for c in opened] == [
        ("open", "service.down", RID)]
    assert "心跳" in opened[0].summary
    remote.heartbeat(5)
    assert [(c.action, c.rule) for c in step(6).changes] == [("close", "service.down")]
    flapping = [r for r in rules_for("service") if r.name == "service.flapping"][0]
    history = store.list_signals(conn, RID)
    assert replay(flapping, history) == []


def test_exits_between_ignores_unreachable_readings() -> None:
    a = Signal(RID, 0, "up", detail={"pid": 1, "reachable": True})
    b = Signal(RID, 60, "unknown", detail={"reachable": False})
    assert exits_between(a, b) == 0 and exits_between(b, a) == 0
    # local readings never carry `reachable`: unchanged
    assert exits_between(Signal("x", 0, "up", detail={"pid": 1}),
                         Signal("x", 60, "down", detail={"pid": None})) == 1


def test_push_failing_rule() -> None:
    def sig(ts: int, failing: bool, ok: bool | None) -> Signal:
        last = None if ok is None else {"ok": ok}
        return Signal(RID, ts, "degraded" if failing else "up",
                      detail={"push": {"failing": failing, "last": last, "fails": []}})

    (r,) = [r for r in rules_for("service") if r.name == "remote.push_failing"]
    series = [sig(0, False, None), sig(1, True, False), sig(2, True, False), sig(3, False, True),
              Signal(RID, 4, "unknown", detail={"reachable": False})]
    assert replay(r, series) == [("open", 1), ("close", 3)]
    assert remote_push_failing([Signal("service:launchd:x", 0, "up", detail={})]) is None


def test_fares_block_reads_latest_signal(tmp_path: Path, vault: Path, remote: Remote) -> None:
    conn = store.connect(tmp_path / "state.db")
    run_collector(conn, RemoteCollector(),
                  ctx=CollectContext(now=int(time.time()), vault=vault, run=remote.run))
    (block,) = fares_block(conn)
    assert block["resource"] == RID and block["fares"] == [] and block["status"] == "up"


def test_brief_carries_fares_from_the_store(vault: Path, remote: Remote) -> None:
    from types import SimpleNamespace

    from kg.capture.brief import collect_brief

    config = SimpleNamespace(brief={"aihot": False, "plane": False}, inbox=vault / "no-inbox")
    _, file = collect_brief(vault, config, fetch=False)
    assert "fares" not in json.loads(file.read_text())  # no state store yet
    conn = store.connect(store.db_path(vault))
    run_collector(conn, RemoteCollector(),
                  ctx=CollectContext(now=int(time.time()), vault=vault, run=remote.run))
    conn.close()
    _, file = collect_brief(vault, config, fetch=False)
    (block,) = json.loads(file.read_text())["fares"]
    assert block["resource"] == RID and block["name"] == "机票监控"


def test_fares_card_gap_and_hourly_trend(tmp_path: Path, vault: Path, remote: Remote) -> None:
    from agents.api_routes.services import build_fares, build_remote
    from kg.plane.collectors import run_collector
    from kg.plane.store import connect

    cfg = vault / ".kg" / "plane" / "remote.yaml"
    cfg.write_text(CONFIG.replace(
        "        fares_db: data/prices.db\n",
        "        fares_db: data/prices.db\n"
        f"        routes: {{config: config.yaml, python: {sys.executable}}}\n"), encoding="utf-8")
    dep = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    ret = (datetime.now() + timedelta(days=37)).strftime("%Y-%m-%d")
    (remote.app / "config.yaml").write_text(
        f"users:\n- routes:\n  - {{from: HKG, to: NRT, from_name: 中国香港, to_name: 东京成田,"
        f" dates: ['{dep}'], ret_dates: ['{ret}'], alert_direct: 3000}}\n", encoding="utf-8")
    db = sqlite3.connect(remote.app / "data" / "prices.db")
    db.execute("""CREATE TABLE flight_prices (id INTEGER PRIMARY KEY, platform TEXT NOT NULL,
        from_city TEXT NOT NULL, to_city TEXT NOT NULL, depart_date TEXT NOT NULL,
        price REAL NOT NULL, airline TEXT, flight_no TEXT, depart_time TEXT, arrive_time TEXT,
        fetched_at TEXT NOT NULL, extra TEXT, prov TEXT NOT NULL DEFAULT '')""")
    rt = json.dumps([{"tripType": "RT", "backDate": ret}])

    def add(price: float, fno: str) -> None:
        db.execute("INSERT INTO flight_prices (platform, from_city, to_city, depart_date, price,"
                   " flight_no, fetched_at, extra) VALUES ('ctrip','HKG','NRT',?,?,?,"
                   " datetime('now','localtime'),?)", (dep, price, fno, rt))
        db.commit()

    conn = connect(tmp_path / "state.db")
    now = int(time.time())
    add(3400, "CX500")
    run_collector(conn, RemoteCollector(), ctx=CollectContext(
        now=now - 7200, vault=vault, run=remote.run))
    add(3150, "CX504")
    run_collector(conn, RemoteCollector(), ctx=CollectContext(now=now, vault=vault, run=remote.run))
    [card] = build_fares(conn, now=now)
    assert (card["label"], card["depart"], card["return"]) == ("中国香港→东京成田", dep, ret)
    assert (card["price"], card["flight_no"], card["budget"], card["gap"]) == (
        3150, "CX504", 3000, 150)
    assert [p for _, p in card["trend"]] == [3400, 3150]
    assert {r["label"] for r in build_remote(conn, {})} == {"local.ticket-monitor",
                                                           "local.tg-tunnel"}
