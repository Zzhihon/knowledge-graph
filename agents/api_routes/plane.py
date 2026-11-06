"""Control plane read endpoints over `<vault>/.kg/plane/state.db`, for kg-web and MCP.

The store path comes from the `plane_db_path` dependency, so tests (and anything else) can
point the router at another file with `app.dependency_overrides[plane_db_path]`.
"""

from __future__ import annotations

import sqlite3  # noqa: TC003 - FastAPI resolves the annotations at runtime
import time
from collections.abc import Iterator  # noqa: TC003
from pathlib import Path  # noqa: TC003
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from kg.plane.store import connect, list_incidents, list_resources, list_signals

router = APIRouter(prefix="/plane", tags=["plane"])


def plane_db_path() -> Path:
    from kg.infrastructure.config.loader import load_config
    from kg.plane.store import db_path

    return db_path(load_config().vault_path)


def _conn(path: Annotated[Path, Depends(plane_db_path)]) -> Iterator[sqlite3.Connection]:
    # Reading never creates the store: before the first collect every list is just empty.
    conn = connect(path if path.exists() else ":memory:")
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(_conn)]


@router.get("/resources")
def get_resources(conn: Conn, kind: str | None = Query(None), source: str | None = Query(None),
                  gone: bool = Query(False, description="也列出已消失的资源")) -> dict[str, Any]:
    """Resources with their latest status."""
    items = list_resources(conn, kind=kind, source=source, include_gone=gone)
    return {"items": items, "count": len(items)}


@router.get("/signals")
def get_signals(conn: Conn, resource: str = Query(..., description="资源 id"),
                since: int | None = Query(None, description="Unix 秒；默认 24 小时前"),
                until: int | None = Query(None),
                limit: int = Query(2000, ge=1, le=20000)) -> dict[str, Any]:
    """One resource's signals, oldest first (the latest `limit` within the range)."""
    if since is None:
        since = int(time.time()) - 86400
    rows = list_signals(conn, resource, since=since, until=until, limit=limit)
    items = [s.to_dict() for s in rows]
    return {"resource": resource, "since": since, "items": items, "count": len(items)}


@router.get("/incidents")
def get_incidents(conn: Conn,
                  open: bool = Query(False, description="1 = 只看未关闭的"),  # noqa: A002
                  resource: str | None = Query(None),
                  limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    """Incidents, newest first."""
    items = list_incidents(conn, open_only=open, resource_id=resource, limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/resources/{resource_id:path}")
def get_resource(resource_id: str, conn: Conn) -> dict[str, Any]:
    """One resource by id (ids contain `:`, so the whole rest of the path is the id)."""
    from kg.plane.store import get_resource as fetch

    item = fetch(conn, resource_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"没有资源 {resource_id}")
    return item
