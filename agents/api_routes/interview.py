"""Interview question bank endpoints with job-based SSE streaming for generation."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

router = APIRouter(tags=["interview"])


class GenerateRequest(BaseModel):
    category: str | None = None
    project: str | None = None
    skill_domain: str | None = None
    focus_topic: str | None = None
    count: int = 5


# ---------------------------------------------------------------------------
# Job store for background generation
# ---------------------------------------------------------------------------


@dataclass
class GenerateJob:
    job_id: str
    status: Literal["running", "complete", "error", "cancelled"] = "running"
    params: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)  # append-only, each has seq
    created_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


_jobs: dict[str, GenerateJob] = {}
_jobs_lock = threading.Lock()
_JOB_TTL = 3600  # 1 hour


def _cleanup_expired_jobs() -> None:
    """Remove jobs older than _JOB_TTL."""
    now = time.time()
    with _jobs_lock:
        expired = [jid for jid, j in _jobs.items() if now - j.created_at > _JOB_TTL]
        for jid in expired:
            del _jobs[jid]


def _run_job(job: GenerateJob) -> None:
    """Background worker: consume generator, append events to job store."""
    try:
        from agents.interview import generate_interview_questions

        for event_data in generate_interview_questions(**job.params):
            if job.cancel_event.is_set():
                with job.lock:
                    job.status = "cancelled"
                    seq = len(job.events)
                    job.events.append({
                        "seq": seq,
                        "event": "cancelled",
                        "data": {"message": "已取消"},
                    })
                return

            ed = dict(event_data)
            event_type = ed.pop("event", "info")
            with job.lock:
                seq = len(job.events)
                job.events.append({"seq": seq, "event": event_type, "data": ed})
                if event_type == "complete":
                    job.status = "complete"

    except Exception as exc:
        with job.lock:
            seq = len(job.events)
            job.events.append({
                "seq": seq,
                "event": "error",
                "data": {"message": f"生成流程异常中断: {exc}"},
            })
            job.status = "error"
    else:
        with job.lock:
            if job.status == "running":
                job.status = "complete"


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
# POST /interview/generate — create generation job
# ---------------------------------------------------------------------------

@router.post("/interview/generate")
def generate_questions(req: GenerateRequest) -> JSONResponse:
    """Create a background generation job and return its ID."""
    _cleanup_expired_jobs()

    job = GenerateJob(
        job_id=uuid.uuid4().hex[:12],
        params={
            "category": req.category,
            "project": req.project,
            "skill_domain": req.skill_domain,
            "focus_topic": req.focus_topic,
            "count": req.count,
        },
    )
    with _jobs_lock:
        _jobs[job.job_id] = job

    threading.Thread(target=_run_job, args=(job,), daemon=True).start()

    return JSONResponse({"job_id": job.job_id})


# ---------------------------------------------------------------------------
# GET /interview/generate/{job_id}/stream — reconnectable SSE stream
# ---------------------------------------------------------------------------

@router.get("/interview/generate/{job_id}/stream")
def stream_job(
    job_id: str,
    last_event_id: int = Query(-1),
) -> Response:
    """Stream generation events with reconnection support via last_event_id."""
    job = _jobs.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "job not found"})

    def event_stream():
        cursor = last_event_id + 1
        last_ka = time.time()

        while True:
            with job.lock:
                new_events = job.events[cursor:]
                status = job.status

            for ev in new_events:
                data = {**ev["data"], "seq": ev["seq"]}
                yield (
                    f"id: {ev['seq']}\n"
                    f"event: {ev['event']}\n"
                    f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                )
                cursor = ev["seq"] + 1

            # Terminal state + all events delivered -> close stream
            if status in ("complete", "error", "cancelled") and not new_events:
                return

            if not new_events:
                now = time.time()
                if now - last_ka >= 10:
                    yield ": keepalive\n\n"
                    last_ka = now
                time.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# POST /interview/generate/{job_id}/cancel — cancel running job
# ---------------------------------------------------------------------------

@router.post("/interview/generate/{job_id}/cancel")
def cancel_job(job_id: str) -> JSONResponse:
    """Cancel a running generation job."""
    job = _jobs.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "job not found"})

    job.cancel_event.set()
    return JSONResponse({"job_id": job_id, "status": "cancelled"})
