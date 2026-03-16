"""Search and RAG question-answering endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(tags=["search"])


class QueryRequest(BaseModel):
    query: str
    domain: str | None = None
    type: str | None = None
    top_k: int = 10


class AskRequest(BaseModel):
    question: str
    domain: str | None = None
    top_k: int = 5


@router.post("/query")
def post_query(req: QueryRequest) -> list[dict[str, Any]]:
    """Hybrid semantic search over the knowledge base."""
    from agents.query import search

    filters: dict[str, str] = {}
    if req.domain:
        filters["domain"] = req.domain
    if req.type:
        filters["type"] = req.type

    return search(
        query=req.query,
        filters=filters if filters else None,
        top_k=req.top_k,
    )


@router.post("/ask")
def post_ask(req: AskRequest) -> StreamingResponse:
    """RAG question-answering with SSE streaming."""
    from agents.ask import ask_stream

    def event_generator():
        for chunk in ask_stream(
            question=req.question,
            top_k=req.top_k,
            domain=req.domain,
        ):
            event_type = chunk["type"]
            data = json.dumps(chunk["data"], ensure_ascii=False)
            yield f"event: {event_type}\ndata: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
