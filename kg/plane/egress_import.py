"""Load an egress probe log (ndjson) into the state store and run the store's rules over it.

The probes become signals of `egress:<line>`, appended in time order. The rules then judge each
new signal in turn (`kg.plane.rules.evaluate`), which is what minute-by-minute collection does,
so the incidents in the store are what live collection would have opened and closed. The report
puts them next to `egress_rules.replay` of the same file: the two must agree.

Importing the same file again appends nothing: probes at or before the line's latest signal are
skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kg.plane.egress import Observation, parse_probe_record
from kg.plane.egress_rules import IDENTITY, OUTAGE, replay
from kg.plane.egress_signals import line_id, observation_to_signal
from kg.plane.egress_sleep import PowerEvent, SleepRules, classify, infer_interval
from kg.plane.rules import Change, evaluate
from kg.plane.store import (
    Resource,
    append_signals,
    close_incident,
    connect,
    list_incidents,
    list_signals,
    open_incident,
    transaction,
    upsert_resources,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from kg.plane.store import Signal

Row = tuple[str, int, int | None]  # (rule, opened_at, closed_at)


@dataclass
class ImportReport:
    resource_id: str
    read: int
    appended: int
    skipped: int
    interval_s: int
    asleep: int
    changes: list[Change] = field(default_factory=list)
    store: list[Row] = field(default_factory=list)  # incidents of this line in the store
    replay: list[Row] = field(default_factory=list)  # what egress_rules.replay decides
    backfilled: int = 0  # signals older than anything the store had (a line already running live)


def read_records(lines: Iterable[str], *, line: str, expected_ips: Sequence[str] | None
                 ) -> list[tuple[Observation, dict]]:
    """ndjson rows → (observation, extra fields kept on the signal), sorted by time."""
    out = []
    expected = set(expected_ips or ())
    for text in lines:
        text = text.strip()
        if not text:
            continue
        try:
            rec = json.loads(text)
            obs = parse_probe_record(rec, line=line)
        except (ValueError, KeyError, TypeError):
            continue
        if expected and obs.exit_ip:
            obs = Observation(obs.ts, obs.segments, obs.line, obs.exit_ip, obs.exit_ip in expected,
                              obs.curl_rc, obs.tls_ms, obs.total_ms)
        extra = {"loc": rec.get("loc"), "colo": rec.get("colo"), "source": "import"}
        out.append((obs, extra))
    return sorted(out, key=lambda pair: pair[0].ts)


def _last_wake(wakes: Sequence[int], ts: int) -> int | None:
    before = [w for w in wakes if w <= ts]
    return before[-1] if before else None


def build_signals(records: Sequence[tuple[Observation, dict]], *, resource_id: str,
                  interval_s: int, power_events: Sequence[PowerEvent] = ()) -> list[Signal]:
    observations = [o for o, _ in records]
    classified = classify(observations, power_events=power_events,
                          rules=SleepRules(expected_interval_s=interval_s))
    wakes = sorted(e.ts for e in power_events if e.kind in ("wake", "darkwake"))
    return [
        observation_to_signal(c.obs, resource_id=resource_id, interval_s=interval_s,
                              status=c.status, woke_at=_last_wake(wakes, c.obs.ts), extra=extra)
        for c, (_, extra) in zip(classified, records, strict=True)
    ]


def import_history(lines: Iterable[str], *, db: Path | str, line: str = "default",
                   expected_ips: Sequence[str] | None = None,
                   power_events: Sequence[PowerEvent] = (), interval_s: int | None = None,
                   dry_run: bool = False) -> ImportReport:
    records = read_records(lines, line=line, expected_ips=expected_ips)
    observations = [o for o, _ in records]
    interval = interval_s or infer_interval([o.ts for o in observations])
    rid = line_id(line)
    result = replay(observations, power_events=power_events,
                    sleep_rules=SleepRules(expected_interval_s=interval))
    conn = connect(db)
    try:
        latest = list_signals(conn, rid, limit=1)
        after = latest[-1].ts if latest else None
        first = conn.execute("SELECT MIN(ts) FROM signal WHERE resource_id = ?",
                             (rid,)).fetchone()[0]

        # Newer than the store: appended and judged by the rules, as live collection would.
        # Older than the store (history of a line that is already collected live): backfilled.
        def keep(ts: int) -> bool:
            return after is None or ts > after or ts < first

        fresh = [r for r in records if keep(r[0].ts)]
        # Classify over the whole file so stored statuses see the probes before the cut.
        signals = build_signals(records, resource_id=rid, interval_s=interval,
                                power_events=power_events)
        signals = [s for s in signals if keep(s.ts)]
        newer = sum(1 for s in signals if after is None or s.ts > after)
        report = ImportReport(rid, len(records), len(fresh), len(records) - len(fresh), interval,
                              sum(1 for s in signals if s.status == "asleep"))
        with transaction(conn, rollback=dry_run):
            if signals:
                upsert_resources(conn, [Resource(rid, "egress", line, "egress",
                                                 {"interval_s": interval, "imported": True})],
                                 now=signals[-1].ts)
                append_signals(conn, signals)
                if newer:
                    report.changes = evaluate(conn, {rid: newer}, {rid: "egress"})
                report.backfilled = len(signals) - newer
            if report.backfilled:
                # The rules only ever judge the latest signals, so incidents inside the backfilled
                # span come from the replay of the same file, with its open and close times.
                known = {(i["rule"], i["opened_at"]) for i in list_incidents(conn, resource_id=rid,
                                                                            limit=100_000)}
                for inc in result.incidents:
                    past = inc.closed_at is not None and inc.closed_at < first
                    if not past or (inc.rule, inc.opened_at) in known:
                        continue
                    evidence = {"started_at": inc.started_at, "probes": list(inc.evidence),
                                "backfilled": True}
                    if open_incident(conn, resource_id=rid, rule=inc.rule, opened_at=inc.opened_at,
                                     severity=inc.severity, summary=inc.summary,
                                     evidence=evidence) is not None:
                        close_incident(conn, resource_id=rid, rule=inc.rule,
                                       closed_at=inc.closed_at)
            report.store = sorted(
                (i["rule"], i["opened_at"], i["closed_at"])
                for i in list_incidents(conn, resource_id=rid, limit=100_000)
                if i["rule"] in (OUTAGE, IDENTITY)
            )
    finally:
        conn.close()
    report.replay = sorted((i.rule, i.opened_at, i.closed_at) for i in result.incidents)
    return report
