"""`GET /api/plane/egress`: everything the 出口 page shows, per egress line, from the state store.

Per line: the latest classified status and where it broke, a status bar over the last `hours`
(up / down / asleep / degraded / unknown / nodata spans), the latency series, exit IP and
location history, purity results (each with its own `checked_at`; IP stability is computed
here from the probe signals), availability and error budget over 30 days (sleep excluded), and
the line's open incidents.

Exit IPs are returned as stored: the server only listens on 127.0.0.1 and the store is local.
Reading never creates the store. The store path comes from `plane_db_path`, like `/api/plane/*`.
"""

from __future__ import annotations

import sqlite3  # noqa: TC003 - FastAPI resolves the annotations at runtime
import time
from pathlib import Path  # noqa: TC003
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from agents.api_routes.plane import plane_db_path
from kg.plane.egress_signals import (
    availability_dict,
    classify_signals,
    exit_history,
    ip_stability,
    is_probe,
    latency_stats,
    purity_id,
    status_spans,
)
from kg.plane.store import (
    DAY,
    connect,
    list_incidents,
    list_resources,
    list_signals,
)

router = APIRouter(prefix="/plane", tags=["plane"])

AVAILABILITY_DAYS = 30
_MAX_SIGNALS = AVAILABILITY_DAYS * 1440 + 1000  # one-minute probes for 30 days


DbPath = Annotated[Path, Depends(plane_db_path)]


def _purity(conn: sqlite3.Connection, line: str, probes: list, now: int) -> dict[str, Any]:
    rows = list_signals(conn, purity_id(line), until=now, limit=48)
    checks: dict[str, Any] = {}
    for s in rows:  # oldest first: later runs overwrite, a check missing from one run stays
        checks.update((s.detail or {}).get("checks") or {})
    checks["ip_stability"] = ip_stability(probes, now=now)
    last = rows[-1] if rows else None
    return {"checks": checks, "checked_at": last.ts if last else None,
            "status": last.status if last else None}


def line_view(conn: sqlite3.Connection, res: dict[str, Any], *, now: int, hours: int
              ) -> dict[str, Any]:
    rid, line = res["id"], res["name"]
    since = now - AVAILABILITY_DAYS * DAY
    signals = [s for s in list_signals(conn, rid, since=since, until=now, limit=_MAX_SIGNALS)
               if is_probe(s)]
    classified, interval = classify_signals(signals, line)
    start = now - hours * 3600
    recent = [c for c in classified if c.obs.ts >= start]
    recent_signals = [s for s in signals if s.ts >= start]
    target = float((res.get("attrs") or {}).get("availability_target") or 0.995)
    last = classified[-1] if classified else None
    last_sig = signals[-1] if signals else None
    # Open at `now`: the store's open ones, or (looking at history) the ones open back then.
    incidents = [i for r in (rid, purity_id(line))
                 for i in list_incidents(conn, resource_id=r, limit=200)
                 if i["opened_at"] <= now and (i["closed_at"] is None or i["closed_at"] > now)]
    return {
        "id": rid,
        "name": line,
        "attrs": res.get("attrs") or {},
        "interval_s": interval,
        "status": last.status if last else None,
        "fault": last.fault if last else None,
        "reason": last.reason if last else None,
        "last_ts": last.obs.ts if last else None,
        "segments": (last_sig.detail or {}).get("segments") if last_sig else None,
        "node": (last_sig.detail or {}).get("node") if last_sig else None,
        "bar": [{"from": s.start, "to": s.end, "status": s.status}
                for s in status_spans(recent, interval_s=interval, start=start, end=now)],
        "latency": [
            {"ts": c.obs.ts, "total_ms": c.obs.total_ms, "tls_ms": c.obs.tls_ms,
             "node_ms": (s.detail or {}).get("node_delay_ms"), "status": c.status}
            for c, s in zip(recent, recent_signals, strict=True)
        ],
        "latency_stats": latency_stats(recent),
        "exits": exit_history(recent_signals),
        "purity": _purity(conn, line, signals, now),
        "availability": availability_dict(classified, interval_s=interval, target=target,
                                          now=now, window_s=AVAILABILITY_DAYS * DAY),
        "availability_24h": availability_dict(classified, interval_s=interval, target=target,
                                              now=now, window_s=DAY),
        "incidents": incidents,
    }


@router.get("/egress")
def get_egress(path: DbPath, hours: int = Query(24, ge=1, le=24 * 7),
               now: int | None = Query(None, description="Unix 秒；默认现在（回放历史时用）")
               ) -> dict[str, Any]:
    """Every egress line with its page data."""
    now = int(time.time()) if now is None else now
    # Opened here, not in a yield dependency: FastAPI may run that one on another thread, and a
    # sqlite3 connection refuses to be used across threads. Reading never creates the store.
    conn = connect(path if path.exists() else ":memory:")
    try:
        lines = [r for r in list_resources(conn, kind="egress") if not r["id"].endswith(":purity")]
        return {"now": now, "hours": hours,
                "lines": [line_view(conn, r, now=now, hours=hours) for r in lines]}
    finally:
        conn.close()
