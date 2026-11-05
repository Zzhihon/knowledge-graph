"""Egress anomaly rules: open/close incidents and compute availability from classified probes.

Inputs are time-sorted observations of one egress line (see `kg.plane.egress`) after sleep
handling (`kg.plane.egress_sleep.classify`). Outputs are incidents with open/close times, plus
the availability and the error budget left. All functions are pure: the rule framework of the
state store calls them on the recent window and diffs the result with what is already open.

Rules
- `egress.outage` (severity warning): at least `min_failures` consecutive failed probes whose
  covered time (first to last probe, plus one interval) reaches `min_down_s`. With a 1-minute
  probe that is 3 failures = 3 minutes; with the 5-minute legacy probe it is 2 failures = 10
  minutes, the soonest a 5-minute probe can tell a real outage from one bad request. Opening is
  decided only on an on-schedule failure, so a wake-up failure alone never opens one. Closed by
  the first successful probe; `asleep` / `unknown` probes neither extend nor close it.
- `egress.identity` (severity critical): opened on the very first probe whose exit does not match
  the expectation, closed by the first probe whose exit matches again.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kg.plane.egress import FAULT_LABELS, IDENTITY_MISMATCH, Observation
from kg.plane.egress_sleep import Classified, PowerEvent, SleepRules, classify, infer_interval

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

OUTAGE = "egress.outage"
IDENTITY = "egress.identity"
_EVIDENCE_LIMIT = 30  # probes kept per incident; the count is always in the summary


@dataclass(frozen=True)
class OutageRules:
    min_failures: int = 2
    min_down_s: int = 180


@dataclass(frozen=True)
class Incident:
    key: str  # stable across re-evaluations: <rule>:<line>:<started_at>
    rule: str
    line: str
    severity: str  # critical | warning (kg.plane.store.SEVERITIES)
    started_at: int  # first failed probe
    opened_at: int  # when the rule decided (the probe that met the threshold)
    closed_at: int | None
    summary: str
    evidence: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Decision:
    action: str  # open | close
    at: int
    incident: Incident


@dataclass(frozen=True)
class Availability:
    target: float
    window_start: int
    window_end: int
    good_s: int
    bad_s: int
    excluded_s: int  # asleep + unknown, not counted either way
    ratio: float | None  # good / (good + bad)
    budget_s: float  # bad time allowed by the target over the counted time
    budget_left_s: float

    @property
    def budget_left_ratio(self) -> float | None:
        return None if self.budget_s <= 0 else self.budget_left_s / self.budget_s


@dataclass(frozen=True)
class Replay:
    line: str
    interval_s: int
    classified: list[Classified]
    incidents: list[Incident]
    availability: Availability
    asleep: list[Classified] = field(default_factory=list)


def _evidence(c: Classified) -> dict[str, Any]:
    item: dict[str, Any] = {
        "ts": c.obs.ts, "status": c.status, "fault": c.fault, "reason": c.reason
    }
    if c.obs.curl_rc is not None:
        item["curl_rc"] = c.obs.curl_rc
    if c.promoted:
        item["promoted"] = True
    return item


def _outage_summary(streak: list[Classified], total: int) -> str:
    faults = Counter(c.fault for c in streak if c.fault)
    fault = faults.most_common(1)[0][0] if faults else None
    label = FAULT_LABELS.get(fault or "", "原因不明")
    reasons = Counter(c.reason for c in streak if c.reason)
    reason = f"；{reasons.most_common(1)[0][0]}" if reasons else ""
    return f"出口断线：{label}{reason}；连续 {total} 次探测失败"


def outage_incidents(
    classified: Sequence[Classified],
    *,
    interval_s: int,
    rules: OutageRules | None = None,
    line: str = "default",
) -> list[Incident]:
    rules = rules or OutageRules()
    incidents: list[Incident] = []
    streak: list[Classified] = []
    open_: dict[str, Any] | None = None

    def close(at: int | None) -> None:
        nonlocal open_
        if open_ is None:
            return
        evidence = tuple(_evidence(c) for c in open_["probes"][:_EVIDENCE_LIMIT])
        incidents.append(
            Incident(
                key=f"{OUTAGE}:{line}:{open_['started_at']}",
                rule=OUTAGE,
                line=line,
                severity="warning",
                started_at=open_["started_at"],
                opened_at=open_["opened_at"],
                closed_at=at,
                summary=_outage_summary(open_["probes"], len(open_["probes"])),
                evidence=evidence,
            )
        )
        open_ = None

    for c in classified:
        if c.fault == IDENTITY_MISMATCH:
            continue  # its own rule; says nothing about reachability
        if c.wake_start:
            streak = []  # a sleep breaks a run of failures
        if c.status == "down":
            streak.append(c)
            if open_ is not None:
                open_["probes"].append(c)
            elif not c.post_wake:
                covered = streak[-1].obs.ts - streak[0].obs.ts + interval_s
                if len(streak) >= rules.min_failures and covered >= rules.min_down_s:
                    open_ = {
                        "started_at": streak[0].obs.ts,
                        "opened_at": c.obs.ts,
                        "probes": list(streak),
                    }
        elif c.status in ("up", "degraded"):
            streak = []
            close(c.obs.ts)
    close(None)
    return incidents


def identity_incidents(
    classified: Sequence[Classified], *, line: str = "default"
) -> list[Incident]:
    incidents: list[Incident] = []
    probes: list[Classified] = []

    def close(at: int | None) -> None:
        if not probes:
            return
        first = probes[0].obs.ts
        incidents.append(
            Incident(
                key=f"{IDENTITY}:{line}:{first}",
                rule=IDENTITY,
                line=line,
                severity="critical",
                started_at=first,
                opened_at=first,
                closed_at=at,
                summary=f"出口身份与期望不符，连续 {len(probes)} 次探测",
                evidence=tuple(
                    {**_evidence(c), "exit_ip": c.obs.exit_ip} for c in probes[:_EVIDENCE_LIMIT]
                ),
            )
        )
        probes.clear()

    for c in classified:
        if c.fault == IDENTITY_MISMATCH:
            probes.append(c)
        elif c.status == "up":  # identity verified again
            close(c.obs.ts)
    close(None)
    return incidents


def decisions(incidents: Iterable[Incident]) -> list[Decision]:
    """Incidents → open/close decisions in time order (what a live rule run would have emitted)."""
    out = []
    for inc in incidents:
        out.append(Decision("open", inc.opened_at, inc))
        if inc.closed_at is not None:
            out.append(Decision("close", inc.closed_at, inc))
    return sorted(out, key=lambda d: (d.at, d.action != "close"))


def availability(
    classified: Sequence[Classified],
    *,
    interval_s: int,
    target: float = 0.995,
    window_s: int = 30 * 86400,
    now: int | None = None,
) -> Availability:
    """Time-weighted availability over the window; each probe stands for one interval.

    up and degraded count as good, down (identity mismatch included) as bad; asleep and unknown
    are left out, so a night with the lid closed does not eat the error budget.
    """
    end = now if now is not None else (classified[-1].obs.ts if classified else 0)
    start = end - window_s
    good = bad = excluded = 0
    for c in classified:
        if not start < c.obs.ts <= end:
            continue
        if c.status in ("up", "degraded"):
            good += interval_s
        elif c.status == "down":
            bad += interval_s
        else:
            excluded += interval_s
    counted = good + bad
    budget = (1 - target) * counted
    return Availability(
        target=target,
        window_start=start,
        window_end=end,
        good_s=good,
        bad_s=bad,
        excluded_s=excluded,
        ratio=good / counted if counted else None,
        budget_s=budget,
        budget_left_s=budget - bad,
    )


def replay(
    observations: Sequence[Observation],
    *,
    expected_ips: Iterable[str] | None = None,
    power_events: Iterable[PowerEvent] = (),
    sleep_rules: SleepRules | None = None,
    outage_rules: OutageRules | None = None,
    target: float = 0.995,
    window_s: int = 30 * 86400,
    now: int | None = None,
) -> Replay:
    """Run every egress rule over the observations of one line."""
    observations = sorted(observations, key=lambda o: o.ts)
    line = observations[0].line if observations else "default"
    sleep_rules = sleep_rules or SleepRules()
    interval = sleep_rules.expected_interval_s or infer_interval([o.ts for o in observations])
    sleep_rules = SleepRules(interval, sleep_rules.gap_factor, sleep_rules.wake_grace_s)
    classified = classify(
        observations, expected_ips=expected_ips, power_events=power_events, rules=sleep_rules
    )
    incidents = outage_incidents(
        classified, interval_s=interval, rules=outage_rules, line=line
    ) + identity_incidents(classified, line=line)
    incidents.sort(key=lambda i: i.opened_at)
    return Replay(
        line=line,
        interval_s=interval,
        classified=classified,
        incidents=incidents,
        availability=availability(
            classified, interval_s=interval, target=target, window_s=window_s, now=now
        ),
        asleep=[c for c in classified if c.status == "asleep"],
    )
