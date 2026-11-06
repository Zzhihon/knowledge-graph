"""Tell sleep from outage: gaps between probes, optional `pmset -g log` wake records.

A probe that runs right after the machine wakes up often fails (on the 11-05 history every one
of the four wakes failed its first probe), because the network and the proxy are not back yet.
Such a failure is marked `asleep`: it never counts against availability and never opens an
incident by itself.

A probe is *post-wake* when the gap since the previous probe is far beyond the probe interval
(`gap_factor` x interval), or when a Sleep/Wake record shows a wake within `wake_grace_s` before
it. A post-wake failure stays `asleep` unless the next on-schedule probe fails too: then the line
really is down and the post-wake failure is promoted back to `down`, so the outage is dated from
the wake. The promotion only looks backwards from the later probe, so the result for a prefix of
the series never depends on probes that come after it.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

from kg.plane.egress import IDENTITY_MISMATCH, Observation, assess

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


@dataclass(frozen=True)
class SleepRules:
    expected_interval_s: int | None = None  # None: median spacing of the series
    gap_factor: float = 2.5  # a gap above this many intervals means the machine slept
    wake_grace_s: int = 120  # probes this soon after a recorded wake are post-wake


@dataclass(frozen=True)
class PowerEvent:
    ts: int
    kind: str  # sleep | wake | darkwake


@dataclass(frozen=True)
class Classified:
    obs: Observation
    status: str  # up | down | degraded | unknown | asleep
    fault: str | None
    reason: str
    gap_s: int | None  # seconds since the previous probe of the same line
    post_wake: bool  # first probe(s) after a sleep
    wake_start: bool  # the first probe after this sleep
    promoted: bool = False  # post-wake failure kept as down because the next probe failed too


# ── pmset -g log ──────────────────────────────────────────────────────────

# `2026-11-05 12:59:43 +0800 Wake                	DarkWake to FullWake from Deep Idle ...`
_PMSET = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4})\s+(Sleep|Wake|DarkWake)\s"
)


def parse_pmset_log(text: str) -> list[PowerEvent]:
    """Sleep / Wake / DarkWake lines of `pmset -g log` → events sorted by time."""
    events = []
    for raw in text.splitlines():
        m = _PMSET.match(raw.strip())
        if not m:
            continue
        ts = int(datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S %z").timestamp())
        events.append(PowerEvent(ts, m.group(2).lower()))
    return sorted(events, key=lambda e: e.ts)


def sleep_spans(events: Iterable[PowerEvent]) -> list[tuple[int, int | None]]:
    """(slept_at, woke_at) pairs; woke_at is None while still asleep at the end of the log."""
    spans: list[tuple[int, int | None]] = []
    asleep_since: int | None = None
    for e in sorted(events, key=lambda e: e.ts):
        if e.kind == "sleep":
            if asleep_since is None:
                asleep_since = e.ts
        elif asleep_since is not None:
            spans.append((asleep_since, e.ts))
            asleep_since = None
    if asleep_since is not None:
        spans.append((asleep_since, None))
    return spans


# ── classification ────────────────────────────────────────────────────────


def infer_interval(timestamps: Sequence[int], default: int = 60) -> int:
    """The probe interval: median spacing, robust to the few long sleep gaps."""
    deltas = [b - a for a, b in zip(timestamps, timestamps[1:], strict=False) if b > a]
    return int(statistics.median(deltas)) if deltas else default


def _woke_between(wakes: Sequence[int], after: int | None, at: int, grace: int) -> bool:
    return any((after is None or w > after) and 0 <= at - w <= grace for w in wakes)


def classify(
    observations: Sequence[Observation],
    *,
    expected_ips: Iterable[str] | None = None,
    power_events: Iterable[PowerEvent] = (),
    rules: SleepRules | None = None,
) -> list[Classified]:
    """Observations of one line, sorted by time → statuses with sleep taken into account."""
    rules = rules or SleepRules()
    expected = tuple(expected_ips or ())
    interval = rules.expected_interval_s or infer_interval([o.ts for o in observations])
    gap_limit = rules.gap_factor * interval
    wakes = [e.ts for e in power_events if e.kind in ("wake", "darkwake")]
    out: list[Classified] = []
    prev_ts: int | None = None
    for obs in observations:
        gap = None if prev_ts is None else obs.ts - prev_ts
        slept = gap is not None and gap > gap_limit
        woke = _woke_between(wakes, prev_ts, obs.ts, rules.wake_grace_s)
        wake_start = slept or woke
        prev = out[-1] if out else None
        # Still inside the same wake-up: the previous probe was post-wake and we are within grace.
        in_grace = _woke_between(wakes, None, obs.ts, rules.wake_grace_s)
        post_wake = wake_start or bool(prev and prev.post_wake and in_grace)
        verdict = assess(obs, expected)
        status = verdict.status
        if status == "down" and post_wake and verdict.fault != IDENTITY_MISMATCH:
            status = "asleep"
        elif status == "down" and verdict.fault != IDENTITY_MISMATCH:
            _promote_wake_run(out)
        out.append(
            Classified(obs, status, verdict.fault, verdict.reason, gap, post_wake, wake_start)
        )
        prev_ts = obs.ts
    return out


def _promote_wake_run(out: list[Classified]) -> None:
    """An on-schedule failure right after post-wake failures: those were the outage, not sleep."""
    i = len(out) - 1
    while i >= 0 and out[i].status == "asleep":
        out[i] = replace(out[i], status="down", promoted=True)
        if out[i].wake_start:
            break
        i -= 1
