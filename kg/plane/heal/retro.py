"""Turning a closed incident into a `03-Debug` retro: the input for Claude, and checking the result.

Only incidents worth the model's time get one: those that lasted at least `retro_min_minutes`,
or the second (or later) of their kind, where "kind" is the same rule on the same resource.

    build_retro_input  timeline (signal runs around the incident), evidence, actions taken,
                       earlier incidents of the kind; every string scrubbed (keys, links,
                       IPv4 addresses, home paths) before it is written
    write_retro_input  <vault>/.kg/plane/incidents/<id>/retro-input.json
    validate_retro_entry  frontmatter fields, type debug, the sections, a mermaid block
    link_entry         incident.entry_id = the entry's id (`kg plane incident link`)
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import frontmatter

from kg.plane.heal import audit
from kg.plane.heal.config import plane_dir
from kg.plane.redact import redact
from kg.plane.store import get_resource, list_signals

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from kg.plane.heal.config import HealConfig

TZ = ZoneInfo("Asia/Shanghai")
RETRO_EVENT = "incident.retro.ready"
ENTRY_ID = re.compile(r"^ke-\d{8}-[a-z0-9][a-z0-9-]{1,80}$")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_HOME = re.compile(r"/(?:Users|home)/[^/\s\"']+")
REQUIRED_FIELDS = ("id", "title", "type", "domain", "tags", "status", "scope", "source",
                   "confidence", "created", "updated")
REQUIRED_SECTIONS = ("现象", "根因", "修复")


def incident_dir(vault: Path, incident_id: int) -> Path:
    return plane_dir(vault) / "incidents" / str(incident_id)


def scrub_text(text: str) -> str:
    text = redact(text)
    text = _IPV4.sub(lambda m: m.group(0) if m.group(0).startswith("127.") else "‹IP›", text)
    return _HOME.sub("~", text)


def scrub(value: Any) -> Any:
    """Scrub every string in a JSON-like value (keys included)."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {scrub_text(str(k)): scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def _when(ts: int | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds")


def duration_s(incident: dict[str, Any]) -> int | None:
    if incident.get("closed_at") is None:
        return None
    return int(incident["closed_at"]) - int(incident["opened_at"])


def same_kind(conn: sqlite3.Connection, incident: dict[str, Any], *, days: int
              ) -> list[dict[str, Any]]:
    """Earlier incidents of the same rule on the same resource, newest first."""
    rows = conn.execute(
        "SELECT * FROM incident WHERE resource_id = ? AND rule = ? AND id != ? AND opened_at < ?"
        " AND opened_at >= ? ORDER BY opened_at DESC",
        (incident["resource_id"], incident["rule"], incident["id"], incident["opened_at"],
         incident["opened_at"] - days * 86400),
    ).fetchall()
    return [dict(r) for r in rows]


def previous_retro(conn: sqlite3.Connection, incident: dict[str, Any], *, days: int = 365
                   ) -> dict[str, Any] | None:
    """The latest earlier incident of this kind that has a retro entry."""
    return next((i for i in same_kind(conn, incident, days=days) if i.get("entry_id")), None)


def should_retro(conn: sqlite3.Connection, incident: dict[str, Any], config: HealConfig
                 ) -> str | None:
    """Why this closed incident gets a retro ("lasted 25 minutes", "2nd time"), or None."""
    length = duration_s(incident)
    if length is None or incident.get("entry_id"):
        return None
    if (incident.get("evidence") or {}).get("closed", {}).get("reason") == "gone":
        return None  # the resource went away; nothing broke
    earlier = same_kind(conn, incident, days=config.retro_repeat_days)
    if earlier:
        return f"同类第 {len(earlier) + 1} 次"
    if length >= config.retro_min_minutes * 60:
        return f"持续 {length // 60} 分钟"
    return None


def timeline_runs(signals: list[Any]) -> list[dict[str, Any]]:
    """Consecutive signals with one status folded into one step of the timeline."""
    out: list[dict[str, Any]] = []
    for s in signals:
        reason = (s.detail or {}).get("reason") or (s.detail or {}).get("fault") or ""
        if out and out[-1]["status"] == s.status:
            out[-1]["to"] = _when(s.ts)
            out[-1]["count"] += 1
            if reason and reason not in out[-1]["reasons"] and len(out[-1]["reasons"]) < 3:
                out[-1]["reasons"].append(reason)
            continue
        out.append({"from": _when(s.ts), "to": _when(s.ts), "status": s.status, "count": 1,
                    "reasons": [reason] if reason else []})
    return out


def suggested_entry_id(incident: dict[str, Any]) -> str:
    day = datetime.fromtimestamp(incident["opened_at"], TZ).strftime("%Y%m%d")
    slug = re.sub(r"[^a-z0-9]+", "-", incident["rule"].lower()).strip("-")
    return f"ke-{day}-{slug}-incident-{incident['id']}"


def build_retro_input(conn: sqlite3.Connection, vault: Path, incident: dict[str, Any],
                      config: HealConfig, *, why: str = "",
                      notes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    opened, closed = int(incident["opened_at"]), incident.get("closed_at")
    end = int(closed) if closed is not None else opened
    evidence = incident.get("evidence") or {}
    start = int(evidence.get("started_at") or opened) - 30 * 60
    signals = list_signals(conn, incident["resource_id"], since=start, until=end + 10 * 60)
    resource = get_resource(conn, incident["resource_id"]) or {}
    attrs = {k: v for k, v in (resource.get("attrs") or {}).items()
             if k in ("manager", "label", "group", "mode", "port", "about", "owner")}
    actions = [{"at": _when(e.at), "action": e.action, "mode": e.mode, "result": e.result,
                "target": e.target, "detail": e.detail, "undo_of": e.undo_of}
               for e in audit.for_incident(vault, incident["id"])]
    earlier = [{"id": i["id"], "opened_at": _when(i["opened_at"]),
                "closed_at": _when(i["closed_at"]), "summary": i["summary"],
                "entry_id": i.get("entry_id")}
               for i in same_kind(conn, incident, days=config.retro_repeat_days)]
    entry_id = suggested_entry_id(incident)
    data = {
        "incident": {
            "id": incident["id"], "rule": incident["rule"], "severity": incident["severity"],
            "resource": incident["resource_id"], "summary": incident["summary"],
            "opened_at": _when(opened), "closed_at": _when(closed),
            "duration_minutes": None if closed is None else round((end - opened) / 60, 1),
            "first_failure_at": _when(evidence.get("started_at")),
        },
        "why_retro": why,
        "resource": {"id": resource.get("id"), "kind": resource.get("kind"),
                     "name": resource.get("name"), "attrs": attrs},
        "timeline": timeline_runs(signals),
        "evidence": evidence,
        "actions": actions,
        "notifications": notes or [],
        "earlier": earlier,
        "write": {
            "entry_id": entry_id,
            "entry_path": f"03-Debug/{entry_id}.md",
            "link_command": f"kg plane incident link {incident['id']} {entry_id}",
            "result_path": str((incident_dir(vault, incident["id"]) / "result.md")
                               .relative_to(vault)),
        },
    }
    return scrub(data)


def write_retro_input(vault: Path, incident_id: int, data: dict[str, Any]) -> Path:
    folder = incident_dir(vault, incident_id)
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / "retro-input.json"
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, file)
    return file


def retro_event(incident: dict[str, Any], file: Path, vault: Path) -> dict[str, str]:
    return {"id": f"plane-retro-{incident['id']}", "type": RETRO_EVENT,
            "subject": f"incident:{incident['id']}",
            "summary": f"事故 #{incident['id']} 待写复盘：{incident['resource_id']}",
            "detail": str(file.relative_to(vault))}


# ── the entry Claude writes ───────────────────────────────────────────────────────────────────


def validate_retro_entry(text: str) -> list[str]:
    """Problems with a retro entry (empty list = it passes)."""
    problems: list[str] = []
    try:
        post = frontmatter.loads(text)
    except Exception as exc:  # yaml errors come in several types
        return [f"frontmatter 解析不了：{type(exc).__name__}"]
    meta = post.metadata
    if not meta:
        return ["没有 frontmatter"]
    for name in REQUIRED_FIELDS:
        if meta.get(name) in (None, "", []):
            problems.append(f"缺 {name}")
    if meta.get("id") and not ENTRY_ID.match(str(meta["id"])):
        problems.append(f"id {meta['id']} 不是 ke-YYYYMMDD-短名")
    if meta.get("type") and meta["type"] != "debug":
        problems.append(f"type 应该是 debug，不是 {meta['type']}")
    for name in ("domain", "tags"):
        if meta.get(name) is not None and not isinstance(meta[name], list):
            problems.append(f"{name} 应该是列表")
    if "related_topics" in meta:
        problems.append("用 related，不要写 related_topics")
    source = meta.get("source")
    if source is not None:
        if not isinstance(source, dict):
            problems.append("source 应该是对象")
        else:
            for name in ("type", "date", "context"):
                if not source.get(name):
                    problems.append(f"缺 source.{name}")
            if not source.get("incident"):
                problems.append("缺 source.incident（事故编号）")
    confidence = meta.get("confidence")
    if confidence is not None and not (isinstance(confidence, (int, float))
                                       and 0 <= confidence <= 1):
        problems.append("confidence 要在 0 到 1 之间")
    body = post.content
    if not re.search(r"^```mermaid\s*\n.*?(flowchart|graph)\b.*?^```", body, re.M | re.S):
        problems.append("没有 mermaid 因果图（```mermaid flowchart）")
    headings = " ".join(re.findall(r"^#{2,3}\s+(.+)$", body, re.M))
    for section in REQUIRED_SECTIONS:
        if section not in headings:
            problems.append(f"缺「{section}」一节")
    if _IPV4.search(_strip_loopback(text)):
        problems.append("正文里有 IP 地址，要脱敏")
    return problems


def _strip_loopback(text: str) -> str:
    return re.sub(r"\b127\.\d+\.\d+\.\d+\b", "", text)


def find_entry(vault: Path, entry_id: str) -> Path | None:
    direct = vault / "03-Debug" / f"{entry_id}.md"
    if direct.is_file():
        return direct
    return next(iter(vault.glob(f"*/{entry_id}.md")), None)


def link_entry(conn: sqlite3.Connection, incident_id: int, entry_id: str) -> None:
    """Write the retro entry's id back to the incident. Raises ValueError when it does not fit."""
    if not ENTRY_ID.match(entry_id):
        raise ValueError(f"条目 id {entry_id} 不是 ke-YYYYMMDD-短名")
    row = conn.execute("SELECT id FROM incident WHERE id = ?", (incident_id,)).fetchone()
    if row is None:
        raise ValueError(f"没有事故 #{incident_id}")
    conn.execute("UPDATE incident SET entry_id = ? WHERE id = ?", (entry_id, incident_id))


def get_incident(conn: sqlite3.Connection, incident_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM incident WHERE id = ?", (incident_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["evidence"] = json.loads(item["evidence"]) if item["evidence"] else None
    return item


def entry_ref(vault: Path, entry_id: str) -> str:
    """How a card points at an entry: the wiki link plus its path in the vault."""
    path = find_entry(vault, entry_id)
    where = str(path.relative_to(vault)) if path else f"03-Debug/{entry_id}.md"
    return f"[[{entry_id}]]（{where}）"

