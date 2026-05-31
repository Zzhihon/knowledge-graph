"""Search and RAG question-answering endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query
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


@router.get("/typeahead")
def get_typeahead(
    q: str = Query(..., min_length=1, description="Search query"),
    domain: str | None = Query(None),
    type: str | None = Query(None),
    limit: int = Query(15, ge=1, le=50),
) -> dict[str, Any]:
    """BM25 typeahead search via Weaviate — fast, no embedding calls."""
    from agents.config import load_config
    from agents.vector_store import get_vector_store

    config = load_config()
    try:
        store = get_vector_store(config)
    except Exception:
        return {"results": [], "total": 0, "query": q}

    try:
        collection = store._get_collection()
        # Build optional filter
        filter_conditions = []
        if domain:
            from weaviate.classes.query import Filter
            filter_conditions.append(
                Filter.by_property("domain").contains_any([domain])
            )
        if type:
            from weaviate.classes.query import Filter
            filter_conditions.append(
                Filter.by_property("type").equal(type)
            )

        weaviate_filter = None
        if filter_conditions:
            weaviate_filter = filter_conditions[0]
            for fc in filter_conditions[1:]:
                weaviate_filter = weaviate_filter & fc

        from weaviate.classes.query import MetadataQuery
        response = collection.query.bm25(
            query=q,
            query_properties=["title^3", "tags^2", "content"],
            filters=weaviate_filter,
            limit=limit,
            return_metadata=MetadataQuery(score=True),
        )

        results: list[dict[str, Any]] = []
        for obj in response.objects:
            props = obj.properties
            results.append({
                "id": props.get("entry_id", ""),
                "title": props.get("title", ""),
                "domain": props.get("domain", ""),
                "type": props.get("type", ""),
                "depth": props.get("depth", ""),
                "tags": props.get("tags", []),
                "confidence": props.get("confidence"),
                "score": round(obj.metadata.score or 0.0, 4),
            })

        return {"results": results, "total": len(results), "query": q}
    except Exception:
        return {"results": [], "total": 0, "query": q}
    finally:
        store.close()


@router.post("/query")
def post_query(req: QueryRequest) -> list[dict[str, Any]]:
    """Hybrid semantic search over the knowledge base."""
    from kg.application.services.search_service import search

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
