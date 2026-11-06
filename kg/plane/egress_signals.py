"""Egress probes in the state store: signal <-> observation, the anomaly rules, and page data.

A probe of one egress line is stored as one signal on `egress:<line>`. The signal carries the
whole observation in `detail` (segment results, exit, curl exit code, timings, the last wake
time the collector saw), so the rules can rebuild the observations and run the same pure code
as `kg plane egress replay`:

    signals (window) ─▶ observations ─▶ egress_sleep.classify ─▶ egress_rules.outage_incidents
                                                                  egress_rules identity check

That is why a collect-per-minute run and a replay of the whole history decide the same opens
and closes: both go through `classify` and `outage_incidents`, and `kg.plane.rules` steps
through the signals one at a time either way.

The stored `status` is the collector's best guess at write time (a failure right after a wake
is written `asleep`). Rules and pages do not trust it: they re-classify, because whether a
wake-up failure was really the start of an outage is only known after the next probe.

Purity checks go on a separate resource `egress:<line>:purity` (one signal per check run).
The probe rules ignore those signals and the purity rule ignores probe signals.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kg.plane.egress import IDENTITY_MISMATCH, Observation, SegmentResult, assess
from kg.plane.egress_rules import (
    IDENTITY,
    OUTAGE,
    OutageRules,
    availability,
    outage_incidents,
)
from kg.plane.egress_sleep import Classified, PowerEvent, SleepRules, classify, infer_interval
from kg.plane.ids import make_id
from kg.plane.rules import Verdict, rule
from kg.plane.store import Signal

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

PURITY = "egress.purity"
PROBE = "probe"  # detail["kind"] of a probe signal
PURITY_KIND = "purity"  # detail["kind"] of a purity signal
# Signals a rule sees. 120 one-minute probes = 2 hours: far more than the 3 failures an outage
# needs, so cutting the history at the window never changes a decision.
WINDOW = 120
GAP_FACTOR = SleepRules().gap_factor


def line_id(line: str) -> str:
    return make_id("egress", line)


def purity_id(line: str) -> str:
    return make_id("egress", line, "purity")


# ── observation <-> signal ────────────────────────────────────────────────


def _segment_dict(s: SegmentResult) -> dict[str, Any]:
    item: dict[str, Any] = {"status": s.status}
    if s.reason:
        item["reason"] = s.reason
    if s.latency_ms is not None:
        item["ms"] = s.latency_ms
    if s.detail:
        item["detail"] = dict(s.detail)
    return item


def observation_to_signal(
    obs: Observation,
    *,
    resource_id: str,
    interval_s: int,
    status: str | None = None,
    woke_at: int | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Signal:
    """One probe → one signal. `status` defaults to the probe's own verdict (no sleep logic)."""
    verdict = assess(obs)
    detail: dict[str, Any] = {
        "kind": PROBE,
        "interval_s": interval_s,
        "fault": verdict.fault,
        "reason": verdict.reason,
        "segments": {s.segment: _segment_dict(s) for s in obs.segments},
        "exit_ip": obs.exit_ip,
        "ip_ok": obs.ip_ok,
        "curl_rc": obs.curl_rc,
        "tls_ms": obs.tls_ms,
        "total_ms": obs.total_ms,
    }
    if woke_at is not None:
        detail["woke_at"] = woke_at
    if extra:
        detail.update({k: v for k, v in extra.items() if v is not None})
    value = obs.total_ms if obs.total_ms is not None else None
    return Signal(resource_id, obs.ts, status or verdict.status, value, "ms", detail)


def is_probe(s: Signal) -> bool:
    return (s.detail or {}).get("kind") == PROBE and not s.samples


def signal_to_observation(s: Signal, line: str = "default") -> Observation:
    d = s.detail or {}
    segments = tuple(
        SegmentResult(name, seg.get("status", "unknown"), seg.get("reason", ""), seg.get("ms"),
                      seg.get("detail") or {})
        for name, seg in (d.get("segments") or {}).items()
    )
    ip_ok = d.get("ip_ok")
    return Observation(
        ts=s.ts,
        segments=segments,
        line=line,
        exit_ip=d.get("exit_ip"),
        ip_ok=ip_ok if isinstance(ip_ok, bool) else None,
        curl_rc=d.get("curl_rc"),
        tls_ms=d.get("tls_ms"),
        total_ms=d.get("total_ms"),
    )


def wake_events(signals: Iterable[Signal]) -> list[PowerEvent]:
    """Wake times the collector recorded (`detail.woke_at`), once each."""
    seen = sorted({int(w) for s in signals if (w := (s.detail or {}).get("woke_at"))})
    return [PowerEvent(ts, "wake") for ts in seen]


def interval_of(signals: Sequence[Signal]) -> int:
    """The probe interval the collector declared on the latest probe, else the median spacing."""
    for s in reversed(signals):
        declared = (s.detail or {}).get("interval_s")
        if declared:
            return int(declared)
    return infer_interval([s.ts for s in signals])


def classify_signals(signals: Sequence[Signal], line: str = "default"
                     ) -> tuple[list[Classified], int]:
    """Probe signals (oldest first) → classified probes, with sleep taken into account."""
    probes = [s for s in signals if is_probe(s)]
    interval = interval_of(probes)
    classified = classify(
        [signal_to_observation(s, line) for s in probes],
        power_events=wake_events(probes),
        rules=SleepRules(expected_interval_s=interval),
    )
    return classified, interval


# ── rules ─────────────────────────────────────────────────────────────────


def _line_of(s: Signal) -> str:
    parts = s.resource_id.split(":")
    return parts[1] if len(parts) > 1 else "default"


@rule("egress", OUTAGE, window=WINDOW)
def egress_outage(history: Sequence[Signal]) -> Verdict | None:
    """`egress.outage` of `kg.plane.egress_rules`, judged on the latest probe."""
    if not history or not is_probe(history[-1]):
        return None
    line = _line_of(history[-1])
    classified, interval = classify_signals(history, line)
    last = classified[-1]
    if last.fault == IDENTITY_MISMATCH:
        return None  # says nothing about reachability
    if last.status in ("up", "degraded"):
        return Verdict(False, summary="出口恢复", evidence={"ts": last.obs.ts})
    incidents = outage_incidents(classified, interval_s=interval, rules=OutageRules(), line=line)
    if incidents and incidents[-1].closed_at is None:
        inc = incidents[-1]
        faults = Counter(e.get("fault") for e in inc.evidence if e.get("fault"))
        return Verdict(True, inc.severity, inc.summary, {
            "started_at": inc.started_at,
            "ts": last.obs.ts,
            "fault": faults.most_common(1)[0][0] if faults else None,
            "probes": list(inc.evidence[-10:]),
        })
    return None


@rule("egress", IDENTITY, window=1)
def egress_identity(history: Sequence[Signal]) -> Verdict | None:
    """`egress.identity`: open on the first probe whose exit does not match, close on the next up.

    A single probe decides it, so a window of one is exactly the replay's behaviour.
    """
    if not history or not is_probe(history[-1]):
        return None
    last = history[-1]
    obs = signal_to_observation(last, _line_of(last))
    verdict = assess(obs)
    if verdict.fault == IDENTITY_MISMATCH:
        d = last.detail or {}
        return Verdict(True, "critical", "出口身份与期望不符", {
            "ts": last.ts, "exit_ip": obs.exit_ip, "loc": d.get("loc"), "colo": d.get("colo"),
        })
    if verdict.status == "up":
        return Verdict(False, summary="出口身份恢复", evidence={"ts": last.ts})
    return None


@rule("egress", PURITY, window=1)
def egress_purity(history: Sequence[Signal]) -> Verdict | None:
    """`egress.purity`: a reminder while any purity check warns; closes when they all pass."""
    if not history:
        return None
    last = history[-1]
    d = last.detail or {}
    if d.get("kind") != PURITY_KIND:
        return None
    warns = {k: v for k, v in (d.get("checks") or {}).items() if v.get("status") == "warn"}
    if warns:
        summary = "出口纯净度变差：" + "；".join(v.get("summary") or k for k, v in warns.items())
        return Verdict(True, "info", summary, {"ts": last.ts, "checks": sorted(warns)})
    if last.status == "up":
        return Verdict(False, summary="纯净度检查恢复正常", evidence={"ts": last.ts})
    return None


# ── page data ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    status: str  # up | down | degraded | unknown | asleep | nodata


def status_spans(classified: Sequence[Classified], *, interval_s: int, start: int, end: int
                 ) -> list[Span]:
    """A status bar from `start` to `end`: each probe covers one interval; long gaps are sleep.

    Time before the first probe and after the last one (beyond one interval) is `nodata`.
    """
    gap_limit = GAP_FACTOR * interval_s
    raw: list[Span] = []
    cursor = start
    for i, c in enumerate(classified):
        ts = c.obs.ts
        if ts >= end:
            break
        nxt = classified[i + 1].obs.ts if i + 1 < len(classified) else None
        if ts > cursor:
            # Gap before this probe: sleep when the previous probe exists, else no data.
            raw.append(Span(cursor, ts, "asleep" if raw and c.wake_start else "nodata"))
        cover_end = ts + interval_s
        if nxt is not None and nxt - ts <= gap_limit:
            cover_end = nxt
        cover_end = min(cover_end, end)
        if cover_end > max(ts, cursor):
            raw.append(Span(max(ts, cursor), cover_end, c.status))
        cursor = max(cursor, cover_end)
    if cursor < end:
        raw.append(Span(cursor, end, "nodata"))
    merged: list[Span] = []
    for s in raw:
        if s.end <= s.start:
            continue
        if merged and merged[-1].status == s.status and merged[-1].end == s.start:
            merged[-1] = Span(merged[-1].start, s.end, s.status)
        else:
            merged.append(s)
    return merged


def exit_history(signals: Sequence[Signal]) -> list[dict[str, Any]]:
    """Runs of the same exit (ip, location, colo), oldest first."""
    runs: list[dict[str, Any]] = []
    for s in signals:
        d = s.detail or {}
        ip = d.get("exit_ip")
        if not ip:
            continue
        key = (ip, d.get("loc"), d.get("colo"))
        if runs and (runs[-1]["exit_ip"], runs[-1]["loc"], runs[-1]["colo"]) == key:
            runs[-1]["to"] = s.ts
            runs[-1]["probes"] += 1
        else:
            runs.append({"exit_ip": ip, "loc": d.get("loc"), "colo": d.get("colo"),
                         "from": s.ts, "to": s.ts, "probes": 1})
    return runs


def ip_stability(signals: Sequence[Signal], *, now: int, window_s: int = 86400
                 ) -> dict[str, Any]:
    """Purity check "exit IP stable over 24 hours", computed from the probe signals."""
    recent = [s for s in signals if is_probe(s) and s.ts > now - window_s]
    ips = [ip for s in recent if (ip := (s.detail or {}).get("exit_ip"))]
    if not ips:
        return {"status": "unknown", "checked_at": recent[-1].ts if recent else None,
                "summary": "24 小时内没有成功的出口探测", "detail": {"ips": 0, "changes": 0}}
    changes = sum(1 for a, b in zip(ips, ips[1:], strict=False) if a != b)
    distinct = len(set(ips))
    status = "ok" if distinct == 1 else "warn"
    summary = "24 小时内出口 IP 没变" if distinct == 1 else \
        f"24 小时内出现 {distinct} 个出口 IP，变化 {changes} 次"
    return {"status": status, "checked_at": recent[-1].ts, "summary": summary,
            "detail": {"ips": distinct, "changes": changes}}


def _pct(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    return float(statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1])


def latency_stats(classified: Sequence[Classified]) -> dict[str, Any]:
    values = [c.obs.total_ms for c in classified if c.status == "up" and c.obs.total_ms]
    return {"p50": _pct(values, 50), "p95": _pct(values, 95),
            "max": max(values) if values else None, "count": len(values)}


def availability_dict(classified: Sequence[Classified], *, interval_s: int, target: float,
                      now: int, window_s: int) -> dict[str, Any]:
    a = availability(classified, interval_s=interval_s, target=target, window_s=window_s, now=now)
    return {"target": a.target, "window_s": window_s, "ratio": a.ratio, "good_s": a.good_s,
            "bad_s": a.bad_s, "excluded_s": a.excluded_s, "budget_s": a.budget_s,
            "budget_left_s": a.budget_left_s, "budget_left_ratio": a.budget_left_ratio}
