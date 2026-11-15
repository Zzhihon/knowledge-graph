"""The 本地服务 page: services grouped by owner, and a redacted log tail on request.

Also the services on other tailnet machines (the `remote` collector) and, for those that report
them, the airfare card: each watched route's lowest fare against its threshold, with a 7-day
hourly trend rebuilt from the stored signals (raw signals are kept 30 days, so 7 days are whole).

Reads the same state store as `/api/plane/*` (and the same `plane_db_path` dependency, so tests
override one thing). The log endpoint only ever opens the log paths the collector read from the
job's own plist (`attrs.logs`), never a path from the request, and masks secrets before the lines
leave the server.
"""

from __future__ import annotations

import sqlite3  # noqa: TC003 - FastAPI resolves the annotations at runtime
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from agents.api_routes.plane import _conn
from agents.local_guard import require_local
from kg.plane.redact import redact
from kg.plane.store import get_resource, list_incidents, list_resources, list_signals

router = APIRouter(prefix="/plane/services", tags=["plane"])

Conn = Annotated[sqlite3.Connection, Depends(_conn)]
GROUPS = ("mine", "tool", "system")
TAIL_BYTES = 64 * 1024
_WORST = {"down": 0, "degraded": 1, "unknown": 2, "asleep": 3, "up": 4}
TREND_DAYS = 7


def _latest_detail(conn: sqlite3.Connection, rid: str) -> dict[str, Any]:
    rows = list_signals(conn, rid, limit=1)
    return (rows[0].detail or {}) if rows else {}


def _last_restart(conn: sqlite3.Connection, rid: str, started: int | None) -> int | None:
    """When the current process started; failing that, the last reading where the PID changed."""
    if started:
        return int(started)
    prev = None
    restart = None
    for s in list_signals(conn, rid, since=int(time.time()) - 7 * 86400, limit=500):
        pid = (s.detail or {}).get("pid")
        if pid is not None and prev is not None and pid != prev:
            restart = s.ts
        prev = pid if pid is not None else prev
    return restart


def _open_incidents(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    open_by: dict[str, list[dict[str, Any]]] = {}
    for i in list_incidents(conn, open_only=True, limit=2000):
        open_by.setdefault(i["resource_id"], []).append(
            {k: i[k] for k in ("id", "rule", "severity", "summary", "opened_at")})
    return open_by


def build_services(conn: sqlite3.Connection) -> dict[str, Any]:
    resources = list_resources(conn, kind="service", source="services")
    open_by = _open_incidents(conn)
    ports_of: dict[str, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []
    for r in resources:
        a = r["attrs"]
        d = _latest_detail(conn, r["id"])
        item = {
            "id": r["id"], "name": r["name"], "manager": a.get("manager"),
            "label": a.get("label"), "port": a.get("port"), "group": a.get("group") or "system",
            "status": r["status"], "status_ts": r["status_ts"], "about": a.get("about") or "",
            "repo": a.get("repo") or "", "registered": bool(a.get("registered")),
            "alert": bool(a.get("alert")), "mode": a.get("mode"), "loaded": a.get("loaded"),
            "command": a.get("command") or ("/".join(a.get("commands") or []) or None),
            "pid": d.get("pid"), "pids": d.get("pids"), "last_exit": d.get("last_exit"),
            "runs": d.get("runs"), "health": d.get("health"), "expected_port": a.get("port"),
            "addresses": a.get("addresses"), "exposed": a.get("exposed"),
            "inferred": bool(a.get("inferred")), "owner": a.get("owner"),
            "has_log": bool(a.get("logs")), "first_seen": r["first_seen"],
            "incidents": open_by.get(r["id"], []),
        }
        item["last_restart"] = _last_restart(conn, r["id"], d.get("started"))
        if a.get("manager") == "port" and a.get("owner") and not a.get("registered"):
            ports_of.setdefault(f"service:launchd:{a['owner']}", []).append(item)
        else:
            rows.append(item)
    for item in rows:
        own = ports_of.pop(item["id"], [])
        item["ports"] = [{"port": p["port"], "addresses": p["addresses"], "exposed": p["exposed"],
                          "id": p["id"], "incidents": p["incidents"]} for p in own]
        item["incidents"] = item["incidents"] + [i for p in own for i in p["incidents"]]
    rows += [dict(p, ports=[]) for left in ports_of.values() for p in left]  # owner not listed
    rows.sort(key=lambda x: (not x["incidents"], _WORST.get(x["status"] or "", 9),
                             not x["registered"], x["name"].lower()))
    groups = {g: [x for x in rows if x["group"] == g] for g in GROUPS}
    collected = max((r["status_ts"] or 0 for r in resources), default=0) or None
    return {
        "groups": groups, "count": len(rows), "collected_at": collected,
        "incidents": sum(len(x["incidents"]) for x in rows),
        "statuses": {s: sum(1 for x in rows if x["status"] == s) for s in _WORST},
    }


def build_remote(conn: sqlite3.Connection,
                 open_by: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for r in list_resources(conn, kind="service", source="remote"):
        a, d = r["attrs"], _latest_detail(conn, r["id"])
        push = d.get("push") or {}
        rows.append({
            "id": r["id"], "name": r["name"], "host": a.get("host"), "label": a.get("label"),
            "about": a.get("about") or "", "repo": a.get("repo") or "", "status": r["status"],
            "status_ts": r["status_ts"], "reachable": d.get("reachable", True),
            "reason": d.get("reason"), "pid": d.get("pid"), "runs": d.get("runs"),
            "heartbeat_age_s": d.get("heartbeat_age_s"), "push_last": push.get("last"),
            "push_failing": bool(push.get("failing")), "incidents": open_by.get(r["id"], []),
        })
    rows.sort(key=lambda x: (not x["incidents"], _WORST.get(x["status"] or "", 9),
                             x["host"] or "", x["name"].lower()))
    return rows


def _fare_key(f: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(f.get("from_city")), str(f.get("to_city")), str(f.get("depart_date")),
            str(f.get("ret_date") or ""))


def build_fares(conn: sqlite3.Connection, now: int | None = None) -> list[dict[str, Any]]:
    """One row per watched route × departure date × return date, from every remote service
    that reports routes: the latest lowest fare, the threshold and the gap, and the hourly
    lowest over the last week. Fares nobody watches (a manual probe) are left out."""
    now = now or int(time.time())
    out = []
    for r in list_resources(conn, kind="service", source="remote"):
        latest = _latest_detail(conn, r["id"])
        routes = latest.get("routes")
        if not routes:
            continue
        current = {_fare_key(f): f for f in latest.get("fares") or []}
        hourly: dict[tuple[str, str, str, str], dict[int, float]] = {}
        for sig in list_signals(conn, r["id"], since=now - TREND_DAYS * 86400):
            for f in (sig.detail or {}).get("fares") or []:
                try:
                    price = float(f.get("price"))
                except (TypeError, ValueError):
                    continue
                hour = sig.ts - sig.ts % 3600
                bucket = hourly.setdefault(_fare_key(f), {})
                bucket[hour] = min(price, bucket.get(hour, price))
        for route in routes:
            for dep in route.get("dates") or []:
                for ret in route.get("ret_dates") or [""]:
                    key = (route["from"], route["to"], dep, ret)
                    f = current.get(key) or {}
                    price = f.get("price")
                    budget = route.get("budget") or 0
                    out.append({
                        "service": r["id"], "host": r["attrs"].get("host"),
                        "label": f"{route.get('from_name') or route['from']}→"
                                 f"{route.get('to_name') or route['to']}",
                        "from": route["from"], "to": route["to"], "depart": dep,
                        "return": ret or None, "budget": budget or None,
                        "price": price, "platform": f.get("platform"),
                        "flight_no": f.get("flight_no"), "fetched_at": f.get("fetched_at"),
                        "gap": (price - budget) if price is not None and budget else None,
                        "trend": sorted([h, p] for h, p in hourly.get(key, {}).items()),
                    })
    out.sort(key=lambda x: (x["gap"] is None, x["gap"] if x["gap"] is not None else 0,
                            x["depart"], x["label"]))
    return out


@router.get("")
def get_services(conn: Conn) -> dict[str, Any]:
    """Every local service, grouped 本人的 / 工具的 / 系统的, worst first within a group; plus
    the services on other machines and the airfare card."""
    data = build_services(conn)
    data["remote"] = build_remote(conn, _open_incidents(conn))
    data["fares"] = build_fares(conn)
    return data


def tail(path: Path, lines: int) -> list[str]:
    """The last `lines` lines of a file, reading at most the last TAIL_BYTES."""
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(size - TAIL_BYTES, 0))
        chunk = f.read()
    text = chunk.decode("utf-8", errors="replace")
    out = text.splitlines()
    if size > TAIL_BYTES and out:
        out = out[1:]  # the first line is probably cut in the middle
    return out[-lines:]


@router.get("/{resource_id:path}/log", dependencies=[Depends(require_local)])
def get_log(resource_id: str, conn: Conn,
            lines: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    """The end of the job's own log files (from its plist), secrets masked."""
    item = get_resource(conn, resource_id)
    if item is None or item["source"] != "services":
        raise HTTPException(status_code=404, detail=f"没有服务 {resource_id}")
    files = []
    for raw in item["attrs"].get("logs") or []:
        path = Path(str(raw))
        entry: dict[str, Any] = {"path": str(path), "lines": []}
        if not path.is_absolute():
            entry["error"] = "plist 里的日志路径不是绝对路径"
        elif not path.is_file():
            entry["error"] = "文件不存在"
        else:
            try:
                entry["lines"] = [redact(line) for line in tail(path, lines)]
                entry["modified"] = int(path.stat().st_mtime)
            except OSError as exc:
                entry["error"] = f"读不了：{exc.strerror or exc}"
        files.append(entry)
    return {"id": resource_id, "files": files, "redacted": True}
