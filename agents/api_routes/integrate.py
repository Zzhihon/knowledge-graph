"""Smart Integration Gateway API: background job-based entry integration with SSE streaming."""

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

router = APIRouter(tags=["integrate"])


class IntegrateRequest(BaseModel):
    entry_ids: list[str]
    dry_run: bool = False
    skip_interview: bool = False


# ---------------------------------------------------------------------------
# Job store (same pattern as interview.py)
# ---------------------------------------------------------------------------

@dataclass
class IntegrateJob:
    job_id: str
    status: Literal["running", "complete", "error", "cancelled"] = "running"
    params: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


_jobs: dict[str, IntegrateJob] = {}
_jobs_lock = threading.Lock()
_JOB_TTL = 3600


def _cleanup_expired_jobs() -> None:
    now = time.time()
    with _jobs_lock:
        expired = [jid for jid, j in _jobs.items() if now - j.created_at > _JOB_TTL]
        for jid in expired:
            del _jobs[jid]


def _run_job(job: IntegrateJob) -> None:
    """Background worker: consume integration generator, append events to job store."""
    try:
        from agents.integrate import integrate_entries

        for event_data in integrate_entries(**job.params):
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
                "data": {"message": f"整合流程异常: {exc}"},
            })
            job.status = "error"
    else:
        with job.lock:
            if job.status == "running":
                job.status = "complete"


# ---------------------------------------------------------------------------
# POST /integrate — start integration job
# ---------------------------------------------------------------------------

@router.post("/integrate")
def start_integration(req: IntegrateRequest) -> JSONResponse:
    """Create a background integration job and return its ID."""
    _cleanup_expired_jobs()

    job = IntegrateJob(
        job_id=uuid.uuid4().hex[:12],
        params={
            "entry_ids": req.entry_ids,
            "dry_run": req.dry_run,
            "skip_interview": req.skip_interview,
        },
    )
    with _jobs_lock:
        _jobs[job.job_id] = job

    threading.Thread(target=_run_job, args=(job,), daemon=True).start()

    return JSONResponse({"job_id": job.job_id})


# ---------------------------------------------------------------------------
# GET /integrate/{job_id}/stream — reconnectable SSE stream
# ---------------------------------------------------------------------------

@router.get("/integrate/{job_id}/stream")
def stream_job(
    job_id: str,
    last_event_id: int = Query(-1),
) -> Response:
    """Stream integration events with reconnection support via last_event_id."""
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
# POST /integrate/{job_id}/cancel
# ---------------------------------------------------------------------------

@router.post("/integrate/{job_id}/cancel")
def cancel_job(job_id: str) -> JSONResponse:
    """Cancel a running integration job."""
    job = _jobs.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "job not found"})

    job.cancel_event.set()
    return JSONResponse({"job_id": job_id, "status": "cancelled"})


# ---------------------------------------------------------------------------
# GET /integrate/jobs — list recent jobs
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# POST /integrate/all — batch integrate all knowledge entries
# ---------------------------------------------------------------------------

class IntegrateAllRequest(BaseModel):
    dry_run: bool = False
    skip_interview: bool = False
    types: list[str] | None = None  # filter by entry type, e.g. ["principle", "pattern"]
    domains: list[str] | None = None  # filter by domain


@router.post("/integrate/all")
def start_integrate_all(req: IntegrateAllRequest) -> JSONResponse:
    """Collect all knowledge entries and start a batch integration job."""
    from agents.config import load_config
    from agents.utils import load_entries

    _cleanup_expired_jobs()

    config = load_config()
    entries = load_entries(config.vault_path)

    # Filter: exclude interview, problem, moc, course types
    skip_types = {"interview", "problem", "moc", "course"}
    entry_ids = []
    for e in entries:
        meta = e["metadata"]
        etype = str(meta.get("type", ""))
        if etype in skip_types:
            continue
        if req.types and etype not in req.types:
            continue
        domain = meta.get("domain", "")
        if isinstance(domain, list):
            domain = domain[0] if domain else ""
        if req.domains and domain not in req.domains:
            continue
        eid = meta.get("id", "")
        if eid:
            entry_ids.append(eid)

    if not entry_ids:
        return JSONResponse({"job_id": None, "error": "没有符合条件的条目", "total": 0})

    job = IntegrateJob(
        job_id=uuid.uuid4().hex[:12],
        params={
            "entry_ids": entry_ids,
            "dry_run": req.dry_run,
            "skip_interview": req.skip_interview,
        },
    )
    with _jobs_lock:
        _jobs[job.job_id] = job

    threading.Thread(target=_run_job, args=(job,), daemon=True).start()

    return JSONResponse({"job_id": job.job_id, "total": len(entry_ids)})


@router.get("/integrate/checkpoint")
def get_checkpoint() -> dict:
    """Get checkpoint status — how many entries have been integrated."""
    from agents.integrate import _load_checkpoint, _CHECKPOINT_FILE
    done = _load_checkpoint()
    return {
        "done_count": len(done),
        "checkpoint_file": str(_CHECKPOINT_FILE),
        "exists": _CHECKPOINT_FILE.exists(),
    }


@router.delete("/integrate/checkpoint")
def reset_checkpoint() -> dict:
    """Reset the checkpoint (re-integrate all entries from scratch)."""
    from agents.integrate import _CHECKPOINT_FILE
    if _CHECKPOINT_FILE.exists():
        _CHECKPOINT_FILE.unlink()
    return {"message": "Checkpoint cleared"}


def list_jobs() -> list[dict[str, Any]]:
    """List recent integration jobs."""
    _cleanup_expired_jobs()
    result = []
    for job in _jobs.values():
        with job.lock:
            event_count = len(job.events)
        result.append({
            "job_id": job.job_id,
            "status": job.status,
            "params": job.params,
            "event_count": event_count,
            "created_at": job.created_at,
        })
    result.sort(key=lambda j: j["created_at"], reverse=True)
    return result
