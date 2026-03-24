"""Course knowledge endpoints with SSE processing progress."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents.course_knowledge import (
    DEFAULT_WORKERS,
    get_course_stats,
    iter_course_processing,
    list_course_entries,
    list_course_files,
)

router = APIRouter(tags=["course"])


class CourseProcessRequest(BaseModel):
    workers: int = Field(DEFAULT_WORKERS, ge=1, le=6)
    dry_run: bool = False
    quality_check: bool = True
    course_files: list[str] | None = None


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/course/files")
def get_course_files() -> dict[str, Any]:
    return list_course_files()


@router.get("/course/stats")
def get_stats() -> dict[str, Any]:
    return get_course_stats()


@router.get("/course/entries")
def get_entries(
    course_file: str | None = Query(None),
    tag: str | None = Query(None),
    domain: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    return list_course_entries(
        course_file=course_file,
        tag=tag,
        domain=domain,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.post("/course/process")
def process_course(req: CourseProcessRequest) -> StreamingResponse:
    def event_stream():
        for event, payload in iter_course_processing(
            workers=req.workers,
            dry_run=req.dry_run,
            quality_check=req.quality_check,
            course_files=req.course_files,
        ):
            yield _sse(event, payload)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
