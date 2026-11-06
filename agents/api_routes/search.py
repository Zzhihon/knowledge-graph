"""Search and RAG question-answering endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

router = APIRouter(tags=["search"])


class QueryRequest(BaseModel):
    query: str
    domain: str | None = None
    type: str | None = None
    kind: str | None = None  # entry (default) | artifact | all
    top_k: int = 10


class AskRequest(BaseModel):
    question: str
    domain: str | None = None
    top_k: int = 5


@router.get("/typeahead")
def get_typeahead(
    q: str = Query(..., min_length=1, description="Search query"),
    domain: str | None = Query(None),
    entry_type: str | None = Query(None, alias="type"),
    kind: str | None = Query(None, description="entry（默认）、artifact（工件正文）或 all"),
    limit: int = Query(15, ge=1, le=50),
) -> dict[str, Any]:
    """Jieba BM25 over the local index — fast, no model call.

    Artifact pages (#6) are only searched when `kind` asks for them; their hits carry `kind`,
    `url`, `local_file` and a masked `snippet`.

    On failure the result list is empty and `error` says why ({code, message, hint}).
    """
    from kg.retrieval.errors import RetrievalError
    from kg.retrieval.service import search_full

    filters = {k: v for k, v in (("domain", domain), ("type", entry_type), ("kind", kind)) if v}
    try:
        result = search_full(q, top_k=limit, filters=filters or None, mode="bm25",
                             rerank=False)
    except RetrievalError as exc:
        return {"results": [], "total": 0, "query": q, "error": exc.to_dict()}
    results = [
        {
            "id": h.entry_id,
            "title": h.title,
            "domain": ", ".join(h.domain),
            "type": h.type,
            "heading": h.heading,
            "score": round(h.score, 4),
            "kind": h.kind,
        } | ({"url": h.url, "local_file": h.path, "snippet": h.snippet[:160],
              "sensitive": h.sensitive} if h.kind == "artifact" else {})
        for h in result.hits
    ]
    return {"results": results, "total": len(results), "query": q, "warnings": result.warnings}


@router.post("/query", response_model=None)
def post_query(req: QueryRequest) -> list[dict[str, Any]] | JSONResponse:
    """Hybrid search over the knowledge base.

    On failure: HTTP 503 with {"error": {code, message, hint}}; code is one of index_missing,
    index_stale, embedding_failed, rerank_failed, retrieval_failed.
    """
    from kg.application.services.search_service import search
    from kg.retrieval.errors import RetrievalError

    filters: dict[str, str] = {}
    if req.domain:
        filters["domain"] = req.domain
    if req.type:
        filters["type"] = req.type
    if req.kind:
        filters["kind"] = req.kind

    try:
        return search(
            query=req.query,
            filters=filters if filters else None,
            top_k=req.top_k,
        )
    except RetrievalError as exc:
        return JSONResponse(status_code=503, content={"error": exc.to_dict()})


@router.post("/ask")
def post_ask(req: AskRequest) -> StreamingResponse:
    """RAG question-answering with SSE streaming."""
    from kg.application.services.ask_service import ask_stream

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
