"""Sync endpoint: trigger incremental or full index rebuild."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["sync"])


class SyncRequest(BaseModel):
    full: bool = False


@router.post("/sync")
def post_sync(req: SyncRequest) -> dict[str, Any]:
    """Sync vault to Qdrant + SurrealDB indexes."""
    from agents.sync_engine import full_sync, incremental_sync

    if req.full:
        return full_sync()
    return incremental_sync()
