"""Interview question bank endpoints with SSE streaming for generation."""

from __future__ import annotations

import json
import queue
import threading
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(tags=["interview"])


class GenerateRequest(BaseModel):
    category: str | None = None
    project: str | None = None
    skill_domain: str | None = None
    focus_topic: str | None = None
    count: int = 5


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# GET /interview/questions — paginated list with filters
# ---------------------------------------------------------------------------

@router.get("/interview/questions")
def list_questions(
    category: str | None = Query(None),
    project: str | None = Query(None),
    difficulty: str | None = Query(None),
    tag: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """List interview questions with optional filtering and pagination."""
    from agents.config import load_config
    from agents.utils import load_entries

    config = load_config()
    entries = load_entries(config.vault_path, filters={"type": "interview"})

    filtered = []
    for entry in entries:
        meta = entry["metadata"]

        if category and str(meta.get("category", "")) != category:
            continue

        if project and str(meta.get("project", "")) != project:
            continue

        if difficulty and str(meta.get("difficulty", "")).lower() != difficulty.lower():
            continue

        if tag:
            entry_tags = meta.get("tags", [])
            if isinstance(entry_tags, str):
                entry_tags = [entry_tags]
            if tag not in entry_tags:
                continue

        if search:
            title = str(meta.get("title", "")).lower()
            tags = " ".join(str(t) for t in meta.get("tags", []))
            if search.lower() not in title and search.lower() not in tags.lower():
                continue

        filtered.append(entry)

    # Sort by created date (newest first), then title
    filtered.sort(key=lambda e: (
        e["metadata"].get("created", ""),
        e["metadata"].get("title", ""),
    ), reverse=True)

    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = filtered[start:end]

    items = []
    for entry in page_items:
        meta = entry["metadata"]
        items.append({
            "id": meta.get("id", ""),
            "title": meta.get("title", ""),
            "category": meta.get("category", ""),
            "project": meta.get("project"),
            "difficulty": meta.get("difficulty", "medium"),
            "answer_framework": meta.get("answer_framework", ""),
            "domain": meta.get("domain", []),
            "tags": meta.get("tags", []),
            "confidence": meta.get("confidence"),
            "review_date": meta.get("review_date", ""),
            "file_path": str(entry["path"]),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if page_size else 1,
    }


# ---------------------------------------------------------------------------
# GET /interview/stats — aggregate statistics
# ---------------------------------------------------------------------------

@router.get("/interview/stats")
def get_stats() -> dict[str, Any]:
    """Return aggregate statistics about the interview question bank."""
    from agents.interview import get_interview_stats
    return get_interview_stats()


# ---------------------------------------------------------------------------
# GET /interview/categories — category list with counts
# ---------------------------------------------------------------------------

@router.get("/interview/categories")
def list_categories() -> list[dict[str, Any]]:
    """List all interview categories with question counts."""
    from agents.interview import get_interview_categories
    return get_interview_categories()


# ---------------------------------------------------------------------------
# GET /interview/domains — skill domain list from config
# ---------------------------------------------------------------------------

@router.get("/interview/domains")
def list_domains() -> list[dict[str, Any]]:
    """List available skill domains for focused generation."""
    from agents.config import load_config
    config = load_config()
    return [
        {
            "key": dk,
            "label": dv.label,
            "icon": dv.icon,
            "sub_domains": dv.sub_domains,
        }
        for dk, dv in sorted(config.domains.items(), key=lambda x: x[0])
    ]


# ---------------------------------------------------------------------------
# POST /interview/generate — LLM generation with SSE progress
# ---------------------------------------------------------------------------

@router.post("/interview/generate")
def generate_questions(req: GenerateRequest) -> StreamingResponse:
    """Generate interview questions via LLM with SSE streaming progress."""

    def event_stream():
        event_queue: queue.Queue[dict[str, Any] | Exception | object] = queue.Queue()
        done = object()

        def producer() -> None:
            try:
                from agents.interview import generate_interview_questions

                for event_data in generate_interview_questions(
                    category=req.category,
                    project=req.project,
                    skill_domain=req.skill_domain,
                    focus_topic=req.focus_topic,
                    count=req.count,
                ):
                    event_queue.put(event_data)
            except Exception as exc:
                event_queue.put(exc)
            finally:
                event_queue.put(done)

        thread = threading.Thread(target=producer, daemon=True)
        thread.start()

        while True:
            try:
                item = event_queue.get(timeout=10)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue

            if item is done:
                break

            if isinstance(item, Exception):
                yield _sse("error", {"message": f"生成流程异常中断: {item}"})
                break

            event_data = dict(item)
            event_type = event_data.pop("event", "info")
            yield _sse(event_type, event_data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
