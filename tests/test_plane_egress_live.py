"""Egress phase 2: live probe, collector, rules in the state store, history import, purity, API.

End-to-end probes run real `curl` against a fake HTTP proxy on a free loopback port, so they
show the segments tell "proxy process gone" from "upstream broken" without touching the real
proxy client. Nothing here leaves the machine.
"""

from __future__ import annotations

import os
import shutil
import socket
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kg.plane import store
from kg.plane.collectors import CollectContext, Collection, CommandError, run_collector
from kg.plane.collectors.egress import EgressCollector, post_wake
from kg.plane.egress import (
    IDENTITY_MISMATCH,
    PROXY_PROCESS,
    UPSTREAM,
    Observation,
    SegmentResult,
    assess,
    classify_curl_rc,
    iter_probe_file,
)
from kg.plane.egress_import import import_history
from kg.plane.egress_probe import (
    Controller,
    CurlResult,
    LineConfig,
    RealTransport,
    clean_env,
    curl_argv,
    local_get,
    parse_config,
    parse_curl_output,
    parse_trace,
    parse_waketime,
    probe_controller,
    probe_line,
)
from kg.plane.egress_purity import (
    check_direct_leak,
    check_locale,
    compare_profile,
    overall,
    parse_dns_leak,
    parse_lsof_established,
    parse_profile,
)
from kg.plane.egress_rules import IDENTITY, OUTAGE, replay
from kg.plane.egress_signals import (
    PURITY,
    classify_signals,
    line_id,
    observation_to_signal,
    purity_id,
    signal_to_observation,
    status_spans,
)
from kg.plane.egress_sleep import SleepRules
from kg.plane.store import Resource

TZ = ZoneInfo("Asia/Shanghai")
FIXTURE = Path(__file__).parent / "fixtures" / "egress_probe_history.ndjson"
FORBIDDEN_HEADERS = ("authorization", "cookie", "x-api-key", "proxy-authorization")
BASE = 1_800_000_000


def _ts(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ).timestamp())


# ── fake proxy ────────────────────────────────────────────────────────────


@dataclass
class FakeProxy:
    """A tiny HTTP proxy on 127.0.0.1. mode: `answer` (plays proxy and origin) or `drop`
    (accepts, reads the CONNECT, hangs up: what a proxy whose upstream died looks like)."""

    mode: str = "answer"
    exit_ip: str = "192.0.2.7"
    seen: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(5)
                data = b""
                try:
                    while b"\r\n\r\n" not in data:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                except OSError:
                    continue
                head = data.decode("latin-1").split("\r\n\r\n", 1)[0].split("\r\n")
                if not head or not head[0]:
                    continue
                method, target = head[0].split(" ")[:2]
                headers = {}
                for h in head[1:]:
                    k, _, v = h.partition(":")
                    headers[k.strip().lower()] = v.strip()
                self.seen.append({"method": method, "target": target, "headers": headers})
                if self.mode == "drop":
                    continue  # hang up right after the request line
                if method == "CONNECT":
                    conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                elif target.endswith("/cdn-cgi/trace"):
                    body = f"fl=1\nh=x\nip={self.exit_ip}\nloc=US\ncolo=LAX\n".encode()
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n"
                                 b"Connection: close\r\n\r\n%s" % (len(body), body))
                else:
                    conn.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n"
                                 b"Connection: close\r\n\r\n")

    def close(self) -> None:
        self.sock.close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


needs_curl = pytest.mark.skipif(shutil.which("curl") is None, reason="curl not installed")


def _line(proxy: str, **kw: Any) -> LineConfig:
    kw.setdefault("trace_url", "http://trace.invalid/cdn-cgi/trace")
    kw.setdefault("target_hosts", ("http://api.invalid/",))
    return LineConfig("test", proxy, **kw)


@needs_curl
def test_closed_proxy_port_is_the_proxy_process() -> None:
    line = _line(f"http://127.0.0.1:{_free_port()}")
    result = probe_line(line, RealTransport(), now=BASE)
    verdict = assess(result.obs)
    assert (verdict.status, verdict.fault) == ("down", PROXY_PROCESS)
    assert result.obs.segment("local_port").detail["error"] == "ECONNREFUSED"
    assert result.obs.segment("identity") is None  # nothing else was tried


@needs_curl
def test_proxy_that_hangs_up_after_connect_is_the_upstream() -> None:
    proxy = FakeProxy(mode="drop")
    try:
        line = _line(proxy.url, trace_url="https://trace.invalid/cdn-cgi/trace")
        result = probe_line(line, RealTransport(), now=BASE)
    finally:
        proxy.close()
    verdict = assess(result.obs)
    assert (verdict.status, verdict.fault) == ("down", UPSTREAM)
    assert result.obs.segment("local_port").status == "up"
    assert result.obs.curl_rc in (52, 56, 35, 97)
    assert proxy.seen and proxy.seen[0]["method"] == "CONNECT"


@needs_curl
def test_working_line_and_no_credentials_on_the_wire(monkeypatch: pytest.MonkeyPatch,
                                                     tmp_path: Path) -> None:
    # Credentials lying around in the environment and a curlrc must not leak into probes.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".curlrc").write_text('header = "Authorization: Bearer leaked"\n'
                                      'cookie = "session=leaked"\n')
    (tmp_path / ".netrc").write_text("machine trace.invalid login u password leaked\n")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-leaked")
    proxy = FakeProxy()
    try:
        result = probe_line(_line(proxy.url), RealTransport(), now=BASE)
    finally:
        proxy.close()
    assert assess(result.obs).status == "up"
    assert result.obs.exit_ip == "192.0.2.7" and result.extra["loc"] == "US"
    assert result.extra["target_codes"] == {"http://api.invalid/": 401}  # 401 counts as reachable
    assert [s["method"] for s in proxy.seen] == ["GET", "HEAD"]
    for req in proxy.seen:
        assert not set(req["headers"]) & set(FORBIDDEN_HEADERS), req["headers"]
        assert "leaked" not in str(req)


def test_curl_argv_never_carries_credentials() -> None:
    for argv in (curl_argv("http://127.0.0.1:1", "https://x.invalid/"),
                 curl_argv("http://127.0.0.1:1", "https://x.invalid/", head=True)):
        assert argv[:2] == ["curl", "-q"]  # -q first: ~/.curlrc is not read
        assert not {"-H", "--header", "-u", "--user", "-b", "--cookie", "-n", "--netrc",
                    "--oauth2-bearer", "-E", "--cert"} & set(argv)
    env = clean_env()
    assert not {k for k in env if "proxy" in k.lower() or "key" in k.lower()}


def test_controller_secret_only_goes_to_loopback() -> None:
    with pytest.raises(ValueError):
        local_get("http://192.0.2.1:9090/proxies", secret="s")


def test_controller_follows_nested_groups() -> None:
    calls = []

    class T:
        def local_get(self, url: str, secret: str | None):
            calls.append((url, secret))
            if url.endswith("/GLOBAL"):
                return 200, {"now": "Auto"}, 3
            if url.endswith("/Auto"):
                return 200, {"now": "node-1"}, 2
            return 200, {"history": [{"delay": 180}]}, 2

    seg, info = probe_controller(Controller("http://127.0.0.1:1"), T())
    assert seg.status == "up" and info == {"node": "node-1", "node_delay_ms": 180}
    assert [u.rsplit("/", 1)[1] for u, _ in calls] == ["GLOBAL", "Auto", "node-1"]


def test_parsers() -> None:
    out = parse_curl_output("ip=1\n__KG_PROBE__0\t200\t0.01\t0.2\t0.45", 0)
    assert (out.rc, out.http_code, out.tls_ms, out.total_ms, out.body) == (0, 200, 200, 450, "ip=1")
    assert parse_curl_output("\n__KG_PROBE__35\t000\t0\t0\t0.3", 35).http_code is None
    assert parse_trace("ip=192.0.2.1\nloc=US\n") == {"ip": "192.0.2.1", "loc": "US"}
    assert parse_waketime("{ sec = 1793889907, usec = 249088 } Thu Nov  5") == 1793889907
    cfg = parse_config({"lines": [{"name": "a", "proxy": "http://127.0.0.1:1",
                                   "expect_ips": "192.0.2.1, 192.0.2.2"}]})
    assert cfg.lines[0].expect_ips == ("192.0.2.1", "192.0.2.2")
    assert cfg.lines[0].proxy_host_port == ("127.0.0.1", 1)


# ── collector with a scripted transport ───────────────────────────────────


@dataclass
class Scripted:
    """Answers per call: port ok?, trace (rc, ip)."""

    port: list[bool]
    trace: list[tuple[int, str | None]]
    argvs: list[list[str]] = field(default_factory=list)

    def tcp(self, host: str, port: int):
        ok = self.port.pop(0) if len(self.port) > 1 else self.port[0]
        return (True, None, 1) if ok else (False, "ECONNREFUSED", None)

    def curl(self, argv):
        self.argvs.append(list(argv))
        url = argv[-1]
        if url.endswith("/cdn-cgi/trace"):
            rc, ip = self.trace.pop(0) if len(self.trace) > 1 else self.trace[0]
            body = f"ip={ip}\nloc=US\ncolo=LAX\n" if rc == 0 else ""
            return CurlResult(rc, 200 if rc == 0 else None, body, 5, 100, 300)
        return CurlResult(0, 404, "", 5, 100, 200)

    def local_get(self, url, secret):
        return 200, {"now": "node"}, 1


def _vault(tmp_path: Path, extra: str = "") -> Path:
    vault = tmp_path / "vault"
    (vault / ".kg" / "plane").mkdir(parents=True)
    (vault / ".kg" / "plane" / "egress.yaml").write_text(
        "interval_s: 60\nrecheck_delay_s: 75\nlines:\n  - name: main\n"
        "    proxy: http://127.0.0.1:1\n    trace_url: https://trace.invalid/cdn-cgi/trace\n"
        "    target_hosts: [api.invalid]\n" + extra, encoding="utf-8")
    return vault


def _ctx(vault: Path, now: int, wake: int | None = None) -> CollectContext:
    def run(argv, timeout=20):
        if argv[:2] == ["sysctl", "-n"] and wake:
            return f"{{ sec = {wake}, usec = 0 }}"
        raise CommandError("not here")
    return CollectContext(now=now, vault=vault, run=run)


def test_no_config_reports_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KG_EGRESS_CONFIG", raising=False)
    out = EgressCollector(transport=Scripted([True], [(0, "x")])).collect(
        CollectContext(now=BASE, vault=tmp_path))
    assert out.resources == [] and out.scopes == () and out.notes


def test_wake_failure_is_rechecked_in_the_same_run(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KG_EGRESS_CONFIG", raising=False)
    vault = _vault(tmp_path, "purity:\n  enabled: false\n")
    slept: list[float] = []
    t = Scripted([True], [(28, None), (0, "192.0.2.7")])
    col = EgressCollector(transport=t, sleep=slept.append, clock=lambda: BASE + 76)
    conn = store.connect(tmp_path / "state.db")
    res = run_collector(conn, col, ctx=_ctx(vault, BASE, wake=BASE - 20))
    assert slept == [75]
    sigs = store.list_signals(conn, line_id("main"))
    assert [(s.ts, s.status) for s in sigs] == [(BASE, "asleep"), (BASE + 76, "up")]
    assert sigs[0].detail["woke_at"] == BASE - 20 and sigs[1].detail["recheck_of"] == BASE
    assert res.changes == []
    # An on-schedule failure (no wake, no gap) is not rechecked.
    t2 = Scripted([True], [(35, None)])
    col2 = EgressCollector(transport=t2, sleep=slept.append, clock=lambda: BASE + 140)
    run_collector(conn, col2, ctx=_ctx(vault, BASE + 136, wake=BASE - 20))
    assert slept == [75]


def test_wake_failure_that_stays_down_becomes_an_outage(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KG_EGRESS_CONFIG", raising=False)
    vault = _vault(tmp_path, "purity:\n  enabled: false\n")
    conn = store.connect(tmp_path / "state.db")
    clock = [BASE + 76]
    col = EgressCollector(transport=Scripted([True], [(35, None)]), sleep=lambda s: None,
                          clock=lambda: clock[0])
    run_collector(conn, col, ctx=_ctx(vault, BASE, wake=BASE - 60))  # woke 60 s ago, fails twice
    assert store.list_incidents(conn) == []
    res = run_collector(conn, col, ctx=_ctx(vault, BASE + 136, wake=BASE - 60))
    (change,) = res.changes
    assert change.rule == OUTAGE and change.ts == BASE + 136
    (inc,) = store.list_incidents(conn, open_only=True)
    assert inc["evidence"]["started_at"] == BASE  # dated from the wake-up failure


def test_exit_mismatch_opens_a_critical_incident_on_the_first_probe(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KG_EGRESS_CONFIG", raising=False)
    vault = _vault(tmp_path, "    expect_ips: [198.51.100.1]\npurity:\n  enabled: false\n")
    conn = store.connect(tmp_path / "state.db")
    col = EgressCollector(transport=Scripted([True], [(0, "192.0.2.7")]), sleep=lambda s: None)
    res = run_collector(conn, col, ctx=_ctx(vault, BASE))
    (change,) = res.changes
    assert (change.action, change.rule, change.severity, change.ts) == \
        ("open", IDENTITY, "critical", BASE)


def test_dry_run_with_a_temporary_expectation(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    # What the acceptance run does on the real exit: config elsewhere, store untouched.
    cfg = tmp_path / "tmp-egress.yaml"
    cfg.write_text("lines:\n  - name: main\n    proxy: http://127.0.0.1:1\n"
                   "    expect_ips: [198.51.100.1]\npurity:\n  enabled: false\n")
    monkeypatch.setenv("KG_EGRESS_CONFIG", str(cfg))
    conn = store.connect(":memory:")
    col = EgressCollector(transport=Scripted([True], [(0, "192.0.2.7")]), sleep=lambda s: None)
    res = run_collector(conn, col, ctx=_ctx(tmp_path, BASE), dry_run=True)
    assert [(c.rule, c.severity) for c in res.changes] == [(IDENTITY, "critical")]
    assert store.list_incidents(conn) == []
    assert (tmp_path / "egress-cache.json").exists()


def test_post_wake_rules() -> None:
    cfg = parse_config({"lines": []})
    assert post_wake(BASE, last_probe=BASE - 200, woke_at=None, cfg=cfg)
    assert not post_wake(BASE, last_probe=BASE - 60, woke_at=None, cfg=cfg)
    assert post_wake(BASE, last_probe=BASE - 60, woke_at=BASE - 100, cfg=cfg)
    assert not post_wake(BASE, last_probe=BASE - 60, woke_at=BASE - 500, cfg=cfg)


# ── store rules vs replay ─────────────────────────────────────────────────


@dataclass
class OneByOne:
    """Replays a probe log through the real collector path, one probe per run."""

    name = "egress"
    signals: list

    def collect(self, ctx: CollectContext) -> Collection:
        s = self.signals.pop(0)
        return Collection([Resource(s.resource_id, "egress", "default", "egress")], [s])


def _store_rows(conn) -> list[tuple[str, int, int | None]]:
    return sorted((i["rule"], i["opened_at"], i["closed_at"])
                  for i in store.list_incidents(conn) if i["rule"] in (OUTAGE, IDENTITY))


def _per_run(tmp_path: Path, observations: list[Observation], interval: int):
    conn = store.connect(tmp_path / "per-run.db")
    rid = line_id("default")
    sigs = [observation_to_signal(o, resource_id=rid, interval_s=interval) for o in observations]
    col = OneByOne(sigs)
    for s in list(sigs):
        run_collector(conn, col, ctx=CollectContext(now=s.ts), compact_after=False)
    return conn


def test_collect_per_probe_and_whole_replay_agree_on_history(tmp_path: Path) -> None:
    obs = list(iter_probe_file(FIXTURE))
    conn = _per_run(tmp_path, obs, 301)
    full = replay(obs, sleep_rules=SleepRules(expected_interval_s=301))
    assert _store_rows(conn) == sorted((i.rule, i.opened_at, i.closed_at) for i in full.incidents)
    assert _store_rows(conn) == [(OUTAGE, _ts("2026-11-05 13:04:50"), _ts("2026-11-05 15:00:52"))]


def _up(ts: int, ip: str = "192.0.2.7", ok: bool = True) -> Observation:
    return Observation(ts, (SegmentResult("local_port", "up"), SegmentResult("identity", "up")),
                       exit_ip=ip, ip_ok=ok)


def _down(ts: int, rc: int = 35) -> Observation:
    return Observation(ts, (SegmentResult("local_port", "up"), classify_curl_rc(rc)), curl_rc=rc)


def _gone(ts: int) -> Observation:
    return Observation(ts, (SegmentResult("local_port", "down", "端口没有进程监听"),))


@pytest.mark.parametrize("pattern", [
    "uuddduu", "uud" + "." * 30 + "duuu", "uudddd.....dduu", "uummmuuddddmu", "ugggguuu",
    "uuuu" + "." * 10 + "ddddu", "dddduuuddu",
])
def test_collect_per_probe_and_replay_agree_on_synthetic(tmp_path: Path, pattern: str) -> None:
    obs = []
    for i, ch in enumerate(pattern):
        ts = BASE + i * 60
        obs += {"u": [_up(ts)], "d": [_down(ts)], "m": [_up(ts, "198.51.100.9", False)],
                "g": [_gone(ts)], ".": []}[ch]
    conn = _per_run(tmp_path, obs, 60)
    full = replay(obs, sleep_rules=SleepRules(expected_interval_s=60))
    assert _store_rows(conn) == sorted((i.rule, i.opened_at, i.closed_at) for i in full.incidents)


def test_signal_round_trip_keeps_the_verdict() -> None:
    for o in (_up(BASE), _down(BASE), _gone(BASE), _up(BASE, "198.51.100.9", False)):
        back = signal_to_observation(observation_to_signal(o, resource_id="egress:x",
                                                           interval_s=60))
        assert assess(back) == assess(o)
    assert assess(_up(BASE, "198.51.100.9", False)).fault == IDENTITY_MISMATCH


# ── history import ────────────────────────────────────────────────────────


def _check_import(path: Path, db: Path) -> None:
    with path.open(encoding="utf-8") as f:
        report = import_history(f, db=db)
    assert report.store == report.replay
    ((rule, opened, closed),) = report.store
    assert rule == OUTAGE
    assert opened < _ts("2026-11-05 13:09:00")
    assert closed is not None and closed > _ts("2026-11-05 14:30:00")
    assert report.asleep == 3
    conn = store.connect(db)
    sigs = store.list_signals(conn, line_id("default"))
    assert len(sigs) == report.read == report.appended
    # The three wake-up failures are asleep in the store and never in an incident.
    asleep = [s.ts for s in sigs if s.status == "asleep"]
    assert asleep == [_ts("2026-11-05 14:55:37"), _ts("2026-11-05 20:33:54"),
                      _ts("2026-11-05 22:45:08")]
    (inc,) = store.list_incidents(conn)
    assert inc["evidence"]["started_at"] == _ts("2026-11-05 12:59:46")
    # Importing again appends nothing and changes nothing.
    with path.open(encoding="utf-8") as f:
        again = import_history(f, db=db)
    assert (again.appended, again.changes) == (0, [])


def test_import_fixture_history(tmp_path: Path) -> None:
    _check_import(FIXTURE, tmp_path / "state.db")


@pytest.mark.skipif(not os.environ.get("KG_EGRESS_REPLAY"), reason="KG_EGRESS_REPLAY not set")
def test_import_raw_history(tmp_path: Path) -> None:
    _check_import(Path(os.environ["KG_EGRESS_REPLAY"]), tmp_path / "state.db")


# ── purity ────────────────────────────────────────────────────────────────


def test_ip_profile_parse_and_compare() -> None:
    p = parse_profile('{"status":"success","countryCode":"US","as":"AS64500 Example",'
                      '"org":"Example","timezone":"America/Los_Angeles","proxy":false,'
                      '"hosting":true}')
    assert p["asn"] == "AS64500" and p["hosting"] is True
    assert compare_profile(p, None)[0] == "ok"
    assert compare_profile(p, p)[0] == "ok"
    worse = {**p, "proxy": True}
    status, summary = compare_profile(worse, p)
    assert status == "warn" and "代理" in summary
    assert compare_profile({**p, "asn": "AS64501"}, p)[0] == "warn"
    assert parse_profile('{"status":"fail","message":"private range"}') is None


def test_dns_leak_and_direct_leak_parsers() -> None:
    servers = parse_dns_leak('[{"ip":"198.51.100.1","country":"us","type":"dns"},'
                             '{"ip":"192.0.2.7","country":"us","type":"ip"},'
                             '{"type":"conclusion","ip":"x"}]')
    assert servers == [{"ip": "198.51.100.1", "country": "US", "asn": None}]
    lsof = ("p100\ncclaude\nn127.0.0.1:50000->127.0.0.1:7890\n"
            "n192.168.1.5:50001->1.1.1.1:443\np200\ncother\nn10.0.0.2:1->198.51.100.3:443\n")
    rows = parse_lsof_established(lsof)
    assert rows[1] == (100, "claude", "1.1.1.1")  # a well-known public address

    @dataclass
    class P:
        direct_leak_processes: tuple = ("claude",)

    r = check_direct_leak(P(), lambda argv, timeout=20: lsof, now=BASE, proxy_host="127.0.0.1")
    assert r["status"] == "warn" and r["detail"]["direct"] == 1
    assert check_direct_leak(P(()), lambda *a, **k: "", now=BASE,
                             proxy_host="127.0.0.1")["status"] == "skipped"


def test_locale_is_only_a_hint() -> None:
    prof = {"timezone": "America/Los_Angeles", "country": "US"}
    r = check_locale(prof, now=BASE, local_offset_h=8, lang="zh_CN.UTF-8")
    assert r["status"] == "hint" and "时区" in r["summary"] and "CN" in r["summary"]
    assert overall({"locale": r}) == "up"
    assert check_locale(prof, now=BASE, local_offset_h=-8, lang="en_US.UTF-8")["status"] == "ok"


def test_purity_runs_hourly_and_on_exit_change_and_reminds(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KG_EGRESS_CONFIG", raising=False)
    vault = _vault(tmp_path, "purity:\n  dns_leak: false\n")
    profiles = {"192.0.2.7": '{"status":"success","countryCode":"US","as":"AS64500 A",'
                             '"timezone":"America/Los_Angeles","proxy":false,"hosting":false}',
                "192.0.2.8": '{"status":"success","countryCode":"US","as":"AS64501 B",'
                             '"timezone":"America/Los_Angeles","proxy":true,"hosting":true}'}

    class T(Scripted):
        def curl(self, argv):
            for ip, body in profiles.items():
                if ip in argv[-1] and "ip-api" in argv[-1]:
                    self.argvs.append(list(argv))
                    return CurlResult(0, 200, body)
            return super().curl(argv)

    conn = store.connect(tmp_path / "state.db")
    t = T([True], [(0, "192.0.2.7")])
    run_collector(conn, EgressCollector(transport=t, sleep=lambda s: None), ctx=_ctx(vault, BASE))
    run_collector(conn, EgressCollector(transport=t, sleep=lambda s: None),
                  ctx=_ctx(vault, BASE + 60))
    sigs = store.list_signals(conn, purity_id("main"))
    assert len(sigs) == 1 and sigs[0].detail["reason"] == "hourly"
    checks = sigs[0].detail["checks"]
    assert set(checks) == {"ip_profile", "dns_leak", "direct_leak", "locale"}
    assert all(c["checked_at"] == BASE for c in checks.values())
    # Exit changes to a worse IP: purity runs right away and the reminder opens.
    t.trace = [(0, "192.0.2.8")]
    res = run_collector(conn, EgressCollector(transport=t, sleep=lambda s: None),
                        ctx=_ctx(vault, BASE + 120))
    sigs = store.list_signals(conn, purity_id("main"))
    assert len(sigs) == 2 and sigs[1].detail["reason"] == "exit_changed"
    assert sigs[1].detail["checks"]["ip_profile"]["status"] == "warn"
    assert [(c.rule, c.severity) for c in res.changes] == [(PURITY, "info")]
    assert sum("ip-api" in a[-1] for a in t.argvs) == 2  # first IP looked up once, then cached
    # Every purity request went through the proxy, like the probes.
    assert all(a[a.index("--proxy") + 1] == "http://127.0.0.1:1" for a in t.argvs)


# ── page data / API ───────────────────────────────────────────────────────


def test_status_spans_mark_sleep_and_no_data() -> None:
    obs = [_up(BASE), _up(BASE + 60), _down(BASE + 120), _up(BASE + 1200)]
    sigs = [observation_to_signal(o, resource_id="egress:x", interval_s=60) for o in obs]
    classified, interval = classify_signals(sigs)
    spans = status_spans(classified, interval_s=interval, start=BASE - 600, end=BASE + 2000)
    assert [(s.status, s.start, s.end) for s in spans] == [
        ("nodata", BASE - 600, BASE),
        ("up", BASE, BASE + 120),
        ("down", BASE + 120, BASE + 180),  # one interval, then the gap is sleep
        ("asleep", BASE + 180, BASE + 1200),
        ("up", BASE + 1200, BASE + 1260),
        ("nodata", BASE + 1260, BASE + 2000),
    ]


def test_egress_api_on_imported_history(tmp_path: Path) -> None:
    from agents.api_routes import egress
    from agents.api_routes.plane import plane_db_path

    db = tmp_path / "state.db"
    with FIXTURE.open(encoding="utf-8") as f:
        import_history(f, db=db)
    app = FastAPI()
    app.include_router(egress.router, prefix="/api")
    app.dependency_overrides[plane_db_path] = lambda: db
    client = TestClient(app)
    at = _ts("2026-11-05 14:00:00")
    body = client.get("/api/plane/egress", params={"now": at}).json()
    (line,) = body["lines"]
    assert line["status"] == "down" and line["fault"] == UPSTREAM
    assert [i["rule"] for i in line["incidents"]] == [OUTAGE]
    down = [b for b in line["bar"] if b["status"] == "down"]
    assert down and down[0]["from"] == _ts("2026-11-05 12:59:46")
    assert any(b["status"] == "asleep" for b in line["bar"])
    assert line["availability"]["target"] == 0.995 and line["availability"]["bad_s"] > 0
    assert line["purity"]["checks"]["ip_stability"]["checked_at"] is not None
    later = client.get("/api/plane/egress", params={"now": _ts("2026-11-06 01:00:00")}).json()
    assert later["lines"][0]["incidents"] == [] and later["lines"][0]["status"] == "up"
    # No store yet: empty, and no file created.
    app.dependency_overrides[plane_db_path] = lambda: tmp_path / "none" / "state.db"
    assert client.get("/api/plane/egress").json()["lines"] == []
    assert not (tmp_path / "none").exists()
