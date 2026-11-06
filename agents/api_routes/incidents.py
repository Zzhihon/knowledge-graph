"""The 事故 page (#9): open and recently closed incidents, their timeline, heal actions, undo.

    GET  /api/plane/incidents/board?days=7   open + closed in the window, newest first
    POST /api/plane/heal/undo  {"id": "<审计 id>"}   same-origin JSON only (agents/local_guard.py)

The vault and the hub come from dependencies (`heal_vault`, `heal_hub`) so tests can swap them.
The hub's owner key stays in kg.plane.heal.hub_actions; nothing here returns it.
"""

from __future__ import annotations

import time
from pathlib import Path  # noqa: TC003 - FastAPI resolves the annotations at runtime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from agents.local_guard import require_local, require_same_origin_json
from kg.plane.heal import audit, silence
from kg.plane.heal.actions import HealContext, undo
from kg.plane.heal.config import load_config as load_heal_config
from kg.plane.heal.hub_actions import HttpHubActions, HubActions
from kg.plane.heal.index_status import read_index_status
from kg.plane.heal.retro import find_entry, timeline_runs
from kg.plane.store import connect, db_path, list_signals

router = APIRouter(prefix="/plane", tags=["plane"])


def heal_vault() -> Path:
    from kg.infrastructure.config.loader import load_config

    return load_config().vault_path


def heal_hub(vault: Annotated[Path, Depends(heal_vault)]) -> HubActions:
    config = load_heal_config(vault)
    return HttpHubActions(config.hub_config, config.hub_timeout)


Vault = Annotated[Path, Depends(heal_vault)]


def _incident_view(conn: Any, vault: Path, row: dict[str, Any], entries: list[audit.AuditEntry],
                   undone: set[str], names: dict[str, str]) -> dict[str, Any]:
    from kg.interfaces.cli.commands.plane_heal import entry_view

    opened, closed = row["opened_at"], row["closed_at"]
    end = closed or max(int(time.time()), opened)
    signals = list_signals(conn, row["resource_id"], since=opened - 1800, until=end + 600,
                           limit=500)
    entry = find_entry(vault, row["entry_id"]) if row["entry_id"] else None
    evidence = row.get("evidence") or {}
    return {
        "id": row["id"], "rule": row["rule"], "resource_id": row["resource_id"],
        "resource_name": names.get(row["resource_id"], ""), "severity": row["severity"],
        "summary": row["summary"], "opened_at": opened, "closed_at": closed,
        "started_at": evidence.get("started_at"),
        "minutes": round((end - opened) / 60),
        "closed_reason": (evidence.get("closed") or {}).get("reason"),
        "entry_id": row["entry_id"],
        "entry_path": str(entry.relative_to(vault)) if entry else None,
        "timeline": timeline_runs(signals),
        "actions": [entry_view(e, undone) for e in entries if e.incident == row["id"]],
    }


@router.get("/incidents/board", dependencies=[Depends(require_local)])
def incidents_board(vault: Vault, days: int = Query(7, ge=1, le=90),
                    limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    import json

    path = db_path(vault)
    now = int(time.time())
    entries = audit.read(vault)
    undone = audit.undone_ids(entries)
    base = {"silenced": list(silence.active(vault, now=now).values()),
            "index": read_index_status(vault).__dict__, "now": now}
    if not path.exists():
        return {"open": [], "closed": [], **base}
    conn = connect(path)
    try:
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM resource")}
        rows = []
        for r in conn.execute(
                "SELECT * FROM incident WHERE closed_at IS NULL OR closed_at >= ?"
                " ORDER BY opened_at DESC LIMIT ?", (now - days * 86400, limit)):
            item = dict(r)
            item["evidence"] = json.loads(item["evidence"]) if item["evidence"] else None
            rows.append(item)
        views = [_incident_view(conn, vault, r, entries, undone, names) for r in rows]
    finally:
        conn.close()
    return {"open": [v for v in views if v["closed_at"] is None],
            "closed": [v for v in views if v["closed_at"] is not None], **base}


@router.post("/heal/undo", dependencies=[Depends(require_same_origin_json)])
def heal_undo(vault: Vault, hub: Annotated[HubActions, Depends(heal_hub)],
              body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    audit_id = str(body.get("id") or "")
    if not audit_id:
        raise HTTPException(status_code=400, detail="要给审计 id")
    ctx = HealContext(vault, load_heal_config(vault), int(time.time()), hub)
    try:
        outcome = undo(ctx, audit_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if outcome.result != audit.DONE:
        raise HTTPException(status_code=502, detail=outcome.text)
    return {"ok": True, "text": outcome.text, "audit": outcome.audit_id}
