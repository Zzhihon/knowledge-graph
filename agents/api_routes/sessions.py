"""In-progress board endpoints: projects and sessions the `sessions` collector wrote.

Reads the same state store as `agents/api_routes/plane.py` (and honours its `plane_db_path`
override). Never creates the store: before the first collect every list is empty.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path  # noqa: TC003 - FastAPI resolves the annotations at runtime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Query

from agents.api_routes.plane import plane_db_path
from kg.plane.progress import WAITING_WINDOW
from kg.plane.store import connect, list_resources

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator

router = APIRouter(prefix="/plane", tags=["plane"])

COLLECTOR = "sessions"
PERIOD = 300  # the collector runs every 5 minutes
_STAGE_ORDER = {"waiting": 0, "active": 1, "stalled": 2, "idle": 3, "done": 4}

DbPath = Annotated[Path, Depends(plane_db_path)]


@contextmanager
def _open(path: Path) -> Iterator[sqlite3.Connection]:
    # Opened and closed inside the endpoint, on one thread: a yield dependency may close it
    # from another threadpool thread, which sqlite3 refuses under concurrent requests.
    conn = connect(path if path.exists() else ":memory:")
    try:
        yield conn
    finally:
        conn.close()


def _item(row: dict[str, Any]) -> dict[str, Any]:
    return {**row["attrs"], "id": row["id"], "status": row["status"],
            "last_seen": row["last_seen"]}


def _freshness(rows: list[dict[str, Any]], now: int) -> dict[str, Any]:
    collected = max((r["last_seen"] for r in rows), default=None)
    return {"collected_at": collected,
            # Two missed runs: the board may no longer reflect the machine.
            "stale": collected is None or now - collected > 2 * PERIOD + 60}


@router.get("/projects")
def get_projects(path: DbPath) -> dict[str, Any]:
    """Projects in progress, with what needs you on top (waiting sessions, stalled projects)."""
    now = int(time.time())
    with _open(path) as conn:
        projects = [_item(r) for r in list_resources(conn, kind="project", source=COLLECTOR)]
        sessions = [_item(r) for r in list_resources(conn, kind="session", source=COLLECTOR)]
    projects.sort(key=lambda p: (_STAGE_ORDER.get(p.get("stage", ""), 9),
                                 -(p.get("last_action") or 0)))
    waiting = sorted((s for s in sessions if s.get("state") == "waiting"
                      and now - (s.get("last_active") or 0) <= WAITING_WINDOW),
                     key=lambda s: -(s.get("last_active") or 0))
    stalled = [p for p in projects if p.get("stage") == "stalled"]
    return {"items": projects, "count": len(projects),
            "attention": {"waiting": waiting, "stalled": stalled,
                          "waiting_older": sum(1 for s in sessions if s.get("state") == "waiting")
                          - len(waiting)},
            **_freshness(projects + sessions, now)}


@router.get("/sessions")
def get_sessions(path: DbPath, project: str | None = Query(None, description="项目名"),
                 state: str | None = Query(None, description="working/waiting/done/interrupted"),
                 limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    """Sessions of the last 7 days, most recent first."""
    with _open(path) as conn:
        rows = [_item(r) for r in list_resources(conn, kind="session", source=COLLECTOR)]
    items = [s for s in rows if (project is None or s.get("project") == project)
             and (state is None or s.get("state") == state)]
    items.sort(key=lambda s: -(s.get("last_active") or 0))
    return {"items": items[:limit], "count": len(items), **_freshness(rows, int(time.time()))}
