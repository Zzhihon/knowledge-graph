from datetime import datetime
from zoneinfo import ZoneInfo

from kg.plane.egress import (
    IDENTITY_MISMATCH,
    PROXY_API,
    PROXY_PROCESS,
    TARGET,
    UPSTREAM,
    Observation,
    SegmentResult,
    assess,
    classify_curl_rc,
    classify_proxy_api,
    classify_target,
    classify_tcp,
    parse_probe_line,
    parse_probe_lines,
)
from kg.plane.egress_sleep import (
    PowerEvent,
    SleepRules,
    classify,
    infer_interval,
    parse_pmset_log,
    sleep_spans,
)

TZ = ZoneInfo("Asia/Shanghai")


def _ts(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ).timestamp())


def _up(ts: int, **kw) -> Observation:
    return Observation(ts, (SegmentResult("identity", "up"),), ip_ok=True, **kw)


def _down(ts: int, rc: int = 35) -> Observation:
    return Observation(ts, (classify_curl_rc(rc),), curl_rc=rc)


# ── parsing ──────────────────────────────────────────────────────────────


def test_legacy_rows_parse_into_observations() -> None:
    up = parse_probe_line(
        '{"ts":"2026-11-05T12:07:54+0800","status":"up","ip_ok":true,"tls_ms":233,"total_ms":409}'
    )
    down = parse_probe_line('{"ts":"2026-11-05T12:59:46+0800","status":"down","curl_rc":35}')
    bad = parse_probe_line('{"ts":"2026-11-05T13:00:00+08:00","status":"bad","code":"503"}')
    assert up and down and bad
    assert down.ts == _ts("2026-11-05 12:59:46")  # +0800 is local time
    assert (up.ip_ok, up.tls_ms, up.total_ms) == (True, 233, 409)
    assert assess(up).status == "up"
    assert assess(down).fault == UPSTREAM
    assert down.curl_rc == 35
    assert assess(bad).status == "down" and "503" in assess(bad).reason


def test_unreadable_lines_are_dropped_and_rows_sorted() -> None:
    lines = [
        '{"ts":"2026-11-05T12:10:00+0800","status":"up","ip_ok":true}',
        "",
        "not json",
        '{"status":"up"}',
        '{"ts":"2026-11-05T12:05:00+0800","status":"up","ip_ok":true}',
    ]
    got = parse_probe_lines(lines)
    assert [o.ts for o in got] == [_ts("2026-11-05 12:05:00"), _ts("2026-11-05 12:10:00")]


# ── where did it break ───────────────────────────────────────────────────


def test_curl_exit_codes_map_to_segments() -> None:
    assert classify_curl_rc(0).status == "up"
    assert classify_curl_rc(7).segment == "local_port"  # never reached the proxy
    assert classify_curl_rc(5).segment == "local_port"
    for rc in (6, 28, 35, 56, 60, 97):
        seg = classify_curl_rc(rc)
        assert (seg.segment, seg.status) == ("identity", "down"), rc
    assert classify_curl_rc(35, segment="target").segment == "target"
    assert classify_curl_rc(7, segment="target").segment == "local_port"
    assert "123" in classify_curl_rc(123).reason


def test_proxy_process_gone_vs_upstream_broken_vs_wrong_exit() -> None:
    port_down = Observation(1, (classify_tcp(False, "ECONNREFUSED"), classify_curl_rc(7)))
    upstream = Observation(1, (classify_tcp(True), classify_curl_rc(35)))
    wrong = Observation(
        1, (classify_tcp(True), SegmentResult("identity", "up")), exit_ip="exit-b", ip_ok=True
    )
    assert assess(port_down).fault == PROXY_PROCESS
    assert "没有进程监听" in assess(port_down).reason
    assert assess(upstream).fault == UPSTREAM
    # The probe itself thought the exit was fine; the expectation from local config wins.
    assert assess(wrong).status == "up"
    assert assess(wrong, ["exit-a"]).fault == IDENTITY_MISMATCH
    assert assess(wrong, ["exit-a", "exit-b"]).status == "up"


def test_target_and_proxy_api_segments() -> None:
    ok = (classify_tcp(True), SegmentResult("identity", "up"))
    assert classify_target(0, 401).status == "up"  # unauthenticated answer still means reachable
    assert classify_target(0, 404).status == "up"
    assert classify_target(0, 502).status == "down"
    target_down = Observation(1, (*ok, classify_target(35)), ip_ok=True)
    assert assess(target_down).fault == TARGET
    api_down = Observation(1, (*ok, classify_proxy_api(None)), ip_ok=True)
    assert (assess(api_down).status, assess(api_down).fault) == ("degraded", PROXY_API)
    assert classify_proxy_api(200, node="node-1").detail["node"] == "node-1"
    assert assess(Observation(1, (classify_tcp(True),))).status == "unknown"
    assert assess(Observation(1)).status == "unknown"


# ── sleep ────────────────────────────────────────────────────────────────

# `pmset -g log` lines, message tails shortened; Assertions lines must be ignored.
PMSET = "\n".join(
    [
        "Time stamp                Domain              \tMessage",
        "2026-11-05 12:18:02 +0800 Sleep               \tEntering Sleep state due to 'Idle Sleep'",
        "2026-11-05 12:20:11 +0800 Assertions          \tPID 123(backupd) Created PreventUserIdle",
        "2026-11-05 12:59:20 +0800 DarkWake            \tDarkWake from Deep Idle [CDN]",
        "2026-11-05 12:59:43 +0800 Wake                \tDarkWake to FullWake",
        "2026-11-05 23:01:00 +0800 Sleep               \tEntering Sleep state due to Clamshell",
    ]
)


def test_pmset_log_parses_sleep_and_wake() -> None:
    events = parse_pmset_log(PMSET)
    assert [e.kind for e in events] == ["sleep", "darkwake", "wake", "sleep"]
    assert events[0].ts == _ts("2026-11-05 12:18:02")
    spans = sleep_spans(events)
    assert spans == [
        (_ts("2026-11-05 12:18:02"), _ts("2026-11-05 12:59:20")),
        (_ts("2026-11-05 23:01:00"), None),
    ]
    assert parse_pmset_log("garbage\n") == []


def test_interval_is_the_median_spacing() -> None:
    assert infer_interval([0, 300, 601, 900, 9000, 9300]) == 300
    assert infer_interval([5]) == 60


def test_gap_marks_post_wake_failure_asleep() -> None:
    base = 1_000_000
    obs = [_up(base), _up(base + 300), _down(base + 300 + 3000, 28), _up(base + 3600)]
    c = classify(obs)
    assert [x.status for x in c] == ["up", "up", "asleep", "up"]
    assert c[2].wake_start and c[2].gap_s == 3000


def test_post_wake_failure_followed_by_failure_is_the_outage_start() -> None:
    base = 1_000_000
    obs = [_up(base), _up(base + 300), _down(base + 3000), _down(base + 3300), _up(base + 3600)]
    c = classify(obs)
    assert [x.status for x in c] == ["up", "up", "down", "down", "up"]
    assert c[2].promoted and not c[3].promoted


def test_promotion_never_crosses_a_second_sleep() -> None:
    base = 1_000_000
    # fail after sleep 1, sleep again, fail after sleep 2, then an on-schedule failure
    obs = [_up(base), _down(base + 3000), _down(base + 9000), _down(base + 9300)]
    c = classify(obs, rules=SleepRules(expected_interval_s=300))
    assert [x.status for x in c] == ["up", "asleep", "down", "down"]


def test_wake_record_marks_failure_asleep_without_a_gap() -> None:
    # 1-minute probes kept running on schedule (dark wake), but pmset says the machine just woke.
    base = 1_000_000
    obs = [_up(base + i * 60) for i in range(3)] + [_down(base + 180, 28), _up(base + 240)]
    wake = [PowerEvent(base + 170, "wake")]
    assert classify(obs)[3].status == "down"
    with_pmset = classify(obs, power_events=wake)
    assert with_pmset[3].status == "asleep" and with_pmset[3].wake_start


def test_identity_mismatch_is_never_treated_as_sleep() -> None:
    base = 1_000_000
    wrong = Observation(base + 5000, (SegmentResult("identity", "up"),), exit_ip="exit-b")
    c = classify([_up(base), wrong], expected_ips=["exit-a"])
    assert (c[1].status, c[1].fault) == ("down", IDENTITY_MISMATCH)
