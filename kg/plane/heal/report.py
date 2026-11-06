"""`kg plane report --week`: availability, incidents and error budget per resource, as Markdown.

- Availability: share of readings that were `up` or `degraded` among those that were `up`,
  `degraded` or `down` (hourly rollups count as their sample count). `asleep` and `unknown`
  are left out, so a closed laptop lid does not count as an outage.
- Incidents: those overlapping the window; their duration is clipped to the window.
- Error budget: (1 - target) × window, with targets by resource kind from heal.yaml `slo`.
  Spent is the union of this resource's warning / critical incident time, so two rules firing
  together are not counted twice; info incidents (reminders) spend nothing.

Which resources: every egress line, our own services (group `mine`), and anything else that
had a warning / critical incident in the window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from kg.plane.heal.retro import TZ
from kg.plane.store import list_resources

if TYPE_CHECKING:
    import sqlite3

    from kg.plane.heal.config import HealConfig

WEEK = 7 * 86400


@dataclass
class ResourceReport:
    id: str
    kind: str
    name: str
    availability: float | None  # 0..1, None without readings
    readings: int
    incidents: int
    incident_minutes: float
    spent_minutes: float
    budget_minutes: float
    target: float
    rules: list[str] = field(default_factory=list)

    @property
    def budget_left(self) -> float:
        if self.budget_minutes <= 0:
            return 0.0
        return max(0.0, 1 - self.spent_minutes / self.budget_minutes)


def _union(spans: list[tuple[int, int]]) -> int:
    total, end = 0, None
    start = None
    for a, b in sorted(spans):
        if end is None or a > end:
            if end is not None and start is not None:
                total += end - start
            start, end = a, b
        else:
            end = max(end, b)
    if end is not None and start is not None:
        total += end - start
    return total


def _availability(conn: sqlite3.Connection, rid: str, start: int, end: int
                  ) -> tuple[float | None, int]:
    good = bad = 0
    for row in conn.execute(
            "SELECT status, samples FROM signal WHERE resource_id = ? AND ts >= ? AND ts < ?",
            (rid, start, end)):
        n = row["samples"] or 1
        if row["status"] in ("up", "degraded"):
            good += n
        elif row["status"] == "down":
            bad += n
    total = good + bad
    return (good / total if total else None), total


def weekly_report(conn: sqlite3.Connection, config: HealConfig, *, now: int,
                  days: int = 7) -> list[ResourceReport]:
    end, start = now, now - days * 86400
    window_min = (end - start) / 60
    incidents: dict[str, list[dict[str, Any]]] = {}
    for row in conn.execute(
            "SELECT * FROM incident WHERE opened_at < ? AND (closed_at IS NULL OR closed_at > ?)",
            (end, start)):
        incidents.setdefault(row["resource_id"], []).append(dict(row))
    resources = {r["id"]: r for r in list_resources(conn, include_gone=True)}
    chosen = []
    for rid, r in resources.items():
        serious = any(i["severity"] != "info" for i in incidents.get(rid, []))
        is_line = r["kind"] == "egress" and rid.count(":") == 1
        mine = r["kind"] == "service" and (r.get("attrs") or {}).get("group") == "mine"
        live = r.get("gone_at") is None or r["gone_at"] > start
        if serious or ((is_line or mine) and live):
            chosen.append(r)
    out = []
    for r in chosen:
        rid = r["id"]
        mine_incidents = incidents.get(rid, [])
        spans = [(max(i["opened_at"], start), min(i["closed_at"] or end, end))
                 for i in mine_incidents]
        serious = [(max(i["opened_at"], start), min(i["closed_at"] or end, end))
                   for i in mine_incidents if i["severity"] != "info"]
        availability, readings = _availability(conn, rid, start, end)
        target = config.slo_for(r["kind"])
        out.append(ResourceReport(
            rid, r["kind"], r.get("name") or "", availability, readings, len(mine_incidents),
            round(sum(b - a for a, b in spans) / 60, 1), round(_union(serious) / 60, 1),
            round(window_min * (1 - target), 1), target,
            sorted({i["rule"] for i in mine_incidents})))
    out.sort(key=lambda x: (x.budget_left, x.availability if x.availability is not None else 2,
                            x.id))
    return out


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def to_markdown(rows: list[ResourceReport], *, now: int, days: int = 7) -> str:
    start = datetime.fromtimestamp(now - days * 86400, TZ).strftime("%m-%d")
    end = datetime.fromtimestamp(now, TZ).strftime("%m-%d")
    total = sum(r.incidents for r in rows)
    burnt = [r for r in rows if r.spent_minutes > r.budget_minutes]
    lines = [f"### 管控面周报（{start} → {end}）", ""]
    lines.append(f"{len(rows)} 个资源，事故 {total} 次"
                 + (f"；错误预算用完：{'、'.join(r.name or r.id for r in burnt)}" if burnt
                    else "；没有资源用完错误预算") + "。")
    lines += ["", "| 资源 | 可用性 | 事故 | 事故时长 | 错误预算剩余 | 规则 |",
              "|---|---:|---:|---:|---:|---|"]
    for r in rows:
        lines.append(
            f"| {r.name or r.id} `{r.id}` | {_pct(r.availability)} | {r.incidents} | "
            f"{r.incident_minutes:g} 分钟 | {r.budget_left * 100:.0f}%"
            f"（{r.spent_minutes:g}/{r.budget_minutes:g} 分钟，目标 {r.target * 100:g}%） | "
            f"{'、'.join(r.rules) or '—'} |")
    lines += ["", "可用性不计休眠和未知时段；错误预算只算 warning / critical 事故的时长。"]
    return "\n".join(lines) + "\n"


def brief_block(vault: Any, config: HealConfig, *, since: int, now: int) -> dict[str, Any] | None:
    """The daily brief's control-plane section, or None when there is no state store yet.

    `incidents`: opened or closed since the last brief; `retros`: retro entries linked to an
    incident since then (with their path, so the brief can link them); `week`: the weekly
    report as Markdown.
    """
    from kg.plane.heal.retro import find_entry
    from kg.plane.store import connect, db_path

    path = db_path(vault)
    if not path.exists():
        return None
    conn = connect(path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, resource_id, rule, severity, summary, opened_at, closed_at, entry_id"
            " FROM incident WHERE opened_at >= ? OR closed_at >= ? ORDER BY opened_at",
            (since, since))]
        week = to_markdown(weekly_report(conn, config, now=now), now=now)
    finally:
        conn.close()
    retros = []
    for r in rows:
        if r["entry_id"]:
            entry = find_entry(vault, r["entry_id"])
            retros.append({"incident": r["id"], "entry_id": r["entry_id"],
                           "path": str(entry.relative_to(vault)) if entry else None})
    return {"incidents": [{**r, "minutes": None if r["closed_at"] is None
                           else round((r["closed_at"] - r["opened_at"]) / 60)} for r in rows],
            "retros": retros, "week": week}
