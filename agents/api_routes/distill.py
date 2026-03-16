"""Distillation endpoints — discover similar entry groups and merge them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(tags=["distill"])


class DistillExecuteRequest(BaseModel):
    entry_ids: list[str]
    dry_run: bool = False


@router.get("/distill/candidates")
def get_distill_candidates(
    threshold: float = Query(0.80, ge=0.0, le=1.0),
    min_group: int = Query(2, ge=2),
    max_group: int = Query(5, ge=2),
) -> list[dict[str, Any]]:
    """Discover groups of similar entries that are candidates for distillation.

    Returns groups sorted by average similarity descending.
    """
    from agents.distill import discover_candidates

    try:
        groups = discover_candidates(
            threshold=threshold,
            min_group=min_group,
            max_group=max_group,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return [
        {
            "group_id": g.group_id,
            "entry_ids": g.entry_ids,
            "titles": g.titles,
            "domains": g.domains,
            "avg_similarity": g.avg_similarity,
        }
        for g in groups
    ]


@router.post("/distill/execute")
def post_distill_execute(body: DistillExecuteRequest) -> dict[str, Any]:
    """Execute distillation: merge entry_ids into a single canonical entry.

    With dry_run=true, previews the result without writing any changes.
    """
    from agents.distill import execute_distill

    if len(body.entry_ids) < 2:
        raise HTTPException(
            status_code=400,
            detail="至少需要两个条目 ID 才能执行蒸馏",
        )

    try:
        result = execute_distill(body.entry_ids, dry_run=body.dry_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "new_entry_id": result.new_entry_id,
        "new_entry_title": result.new_entry_title,
        "new_entry_path": result.new_entry_path,
        "superseded_ids": result.superseded_ids,
        "deleted_count": result.deleted_count,
    }
