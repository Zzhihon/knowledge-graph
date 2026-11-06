"""Replay the egress rules on the 11-05 probe history and on synthetic series.

The fixture is a scrubbed copy of the real probe log (scripts/egress_fixture.py). Point
KG_EGRESS_REPLAY at the raw log to replay the full original as well.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kg.plane.egress import Observation, SegmentResult, classify_curl_rc, iter_probe_file
from kg.plane.egress_rules import (
    IDENTITY,
    OUTAGE,
    availability,
    decisions,
    replay,
)
from kg.plane.egress_sleep import PowerEvent, SleepRules, classify

TZ = ZoneInfo("Asia/Shanghai")
FIXTURE = Path(__file__).parent / "fixtures" / "egress_probe_history.ndjson"


def _ts(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ).timestamp())


def _up(ts: int, exit_ip: str | None = None) -> Observation:
    return Observation(ts, (SegmentResult("identity", "up"),), exit_ip=exit_ip, ip_ok=True)


def _down(ts: int, rc: int = 35) -> Observation:
    return Observation(ts, (classify_curl_rc(rc),), curl_rc=rc)


def _check_history(obs: list[Observation]) -> None:
    result = replay(obs)
    outages = [i for i in result.incidents if i.rule == OUTAGE]
    assert len(result.incidents) == 1 and len(outages) == 1
    inc = outages[0]
    # The TLS outage starting 12:59 must be alerted before 13:09 and closed after 14:30.
    assert inc.started_at == _ts("2026-11-05 12:59:46")
    assert inc.opened_at < _ts("2026-11-05 13:09:00")
    assert inc.opened_at == _ts("2026-11-05 13:04:50")
    assert inc.closed_at is not None and inc.closed_at > _ts("2026-11-05 14:30:43")
    assert inc.closed_at == _ts("2026-11-05 15:00:52")
    assert all(e.get("curl_rc") == 35 for e in inc.evidence) and len(inc.evidence) == 19
    # The other three wake-up failures are sleep, not outages.
    asleep = [c.obs.ts for c in result.asleep]
    assert asleep == [
        _ts("2026-11-05 14:55:37"),
        _ts("2026-11-05 20:33:54"),
        _ts("2026-11-05 22:45:08"),
    ]
    assert {c.obs.curl_rc for c in result.asleep} == {28, 35}
    assert result.interval_s == 301


def test_fixture_replay_alerts_on_outage_and_ignores_wakeups() -> None:
    _check_history(list(iter_probe_file(FIXTURE)))


def test_fixture_carries_no_identifying_fields() -> None:
    allowed = {"ts", "status", "curl_rc", "tls_ms", "total_ms", "ip_ok"}
    for text in FIXTURE.read_text(encoding="utf-8").splitlines():
        rec = json.loads(text)
        assert set(rec) <= allowed
        assert not isinstance(rec.get("ip_ok"), str)


def test_fixture_availability_excludes_sleep() -> None:
    result = replay(list(iter_probe_file(FIXTURE)))
    statuses = [c.status for c in result.classified]
    a = result.availability
    assert a.excluded_s == statuses.count("asleep") * 301
    assert a.bad_s == statuses.count("down") * 301 == 19 * 301
    assert a.ratio == pytest.approx(statuses.count("up") / (statuses.count("up") + 19))
    assert a.budget_left_s < 0  # 1.5 hours down blows a 99.5% budget


def test_live_run_on_every_prefix_decides_the_same() -> None:
    # A rule run only ever sees the probes so far; it must not change its mind about the past.
    obs = list(iter_probe_file(FIXTURE))
    full = replay(obs).incidents[0]
    first_open = None
    for k in range(1, len(obs) + 1):
        incs = replay(obs[:k], sleep_rules=SleepRules(expected_interval_s=301)).incidents
        if incs and first_open is None:
            first_open = obs[k - 1].ts
        for inc in incs:
            assert (inc.started_at, inc.opened_at) == (full.started_at, full.opened_at)
    assert first_open == full.opened_at


@pytest.mark.skipif(not os.environ.get("KG_EGRESS_REPLAY"), reason="KG_EGRESS_REPLAY not set")
def test_raw_history_replay() -> None:
    _check_history(list(iter_probe_file(Path(os.environ["KG_EGRESS_REPLAY"]))))


# ── synthetic series (1-minute probe) ─────────────────────────────────────

BASE = 1_800_000_000


def _series(pattern: str, start: int = BASE, step: int = 60) -> list[Observation]:
    """'u' up, 'd' down, '.' no probe (machine asleep)."""
    out = []
    for i, ch in enumerate(pattern):
        if ch == "u":
            out.append(_up(start + i * step))
        elif ch == "d":
            out.append(_down(start + i * step))
    return out


def test_single_failure_never_alerts() -> None:
    assert replay(_series("uuuduuu")).incidents == []
    assert replay(_series("uuuduuu", step=300)).incidents == []


def test_three_minutes_of_failures_alert_on_the_third() -> None:
    assert replay(_series("uudduu")).incidents == []  # 2 minutes
    (inc,) = replay(_series("uuddduu")).incidents
    assert inc.started_at == BASE + 120
    assert inc.opened_at == BASE + 240
    assert inc.closed_at == BASE + 300


def test_recover_then_break_again_is_a_new_incident() -> None:
    a, b = replay(_series("uddduddddu")).incidents
    assert a.closed_at == BASE + 240 and b.started_at == BASE + 300
    assert a.key != b.key
    assert [d.action for d in decisions([b, a])] == ["open", "close", "open", "close"]


def test_incident_still_open_at_the_end() -> None:
    (inc,) = replay(_series("uudddd")).incidents
    assert inc.closed_at is None


def test_failures_across_a_sleep_do_not_add_up() -> None:
    # down, sleep, down after wake, up: two separate wake-up blips, no outage
    assert replay(_series("uud" + "." * 30 + "du")).incidents == []


def test_wake_up_failures_recorded_by_pmset() -> None:
    wake = [PowerEvent(BASE + 170, "wake")]
    # Two failures inside the 120 s grace after the wake, then up: sleep, not counted.
    blip = replay(_series("uuudduu"), power_events=wake)
    assert blip.incidents == []
    assert [c.status for c in blip.classified].count("asleep") == 2
    assert blip.availability.bad_s == 0
    # Still failing once the grace is over: the line is down, dated from the wake.
    (inc,) = replay(_series("uuuddduu"), power_events=wake).incidents
    assert (inc.started_at, inc.opened_at) == (BASE + 180, BASE + 300)


def test_exit_mismatch_alerts_within_one_minute() -> None:
    # Temporarily expect a different exit than the one the line uses: alarm on the first probe.
    obs = [_up(BASE + i * 60, "exit-a") for i in range(5)]
    obs += [_up(BASE + 300 + i * 60, "exit-b") for i in range(3)]
    obs += [_up(BASE + 480, "exit-a")]
    (inc,) = replay(obs, expected_ips=["exit-a"]).incidents
    assert inc.rule == IDENTITY and inc.severity == "critical"
    changed_at = BASE + 300
    assert inc.opened_at - changed_at <= 60
    assert inc.closed_at == BASE + 480
    # Changing the expectation alone (the line did not change) also alarms immediately.
    (tmp,) = replay(obs[:3], expected_ips=["exit-temp"]).incidents
    assert tmp.opened_at == BASE and tmp.closed_at is None


def test_mismatch_does_not_count_as_outage_or_sleep() -> None:
    obs = [_up(BASE, "exit-a"), _up(BASE + 3000, "exit-b"), _up(BASE + 3060, "exit-b")]
    result = replay(obs, expected_ips=["exit-a"], sleep_rules=SleepRules(expected_interval_s=60))
    assert [i.rule for i in result.incidents] == [IDENTITY]
    assert result.availability.bad_s == 120


def test_availability_window_and_budget() -> None:
    day = 86400
    old = [_down(BASE + i * 60) for i in range(100)]
    recent = [_up(BASE + 40 * day + i * 60) for i in range(1000)] + [
        _down(BASE + 40 * day + 60_000 + i * 60) for i in range(3)
    ]
    c = classify(old + recent, rules=SleepRules(expected_interval_s=60))
    a = availability(c, interval_s=60)
    assert (a.good_s, a.bad_s) == (60_000, 180)  # the old failures fall outside 30 days
    assert a.budget_s == pytest.approx(0.005 * 60_180)
    assert a.budget_left_ratio == pytest.approx(1 - 180 / (0.005 * 60_180))
