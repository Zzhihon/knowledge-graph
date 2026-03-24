"""URL ingest endpoint: fetch a web page and extract knowledge entries."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, HttpUrl

router = APIRouter(tags=["ingest"])


class URLIngestRequest(BaseModel):
    url: HttpUrl
    dry_run: bool = False
    quality_check: bool = True
    timeout: int = 30
    tags: list[str] = Field(default_factory=list)


@router.post("/ingest/url")
def ingest_url(req: URLIngestRequest) -> dict[str, Any]:
    """Fetch a URL, extract content, and run the knowledge extraction pipeline.

    Returns IngestResult-shaped response with extra url/title/content_length fields.
    """
    from agents.sources.web import WebAdapter

    # Phase 1: Fetch and extract content
    adapter = WebAdapter()
    try:
        doc = adapter.fetch_url(
            url=str(req.url),
            timeout=req.timeout,
            extra_tags=req.tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Phase 2: Write to temp file and run ingest pipeline
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as tmp:
            tmp.write(doc.to_markdown())
            tmp_path = Path(tmp.name)

        if req.quality_check:
            from agents.ingest import ingest_file_with_quality

            results = ingest_file_with_quality(
                file_path=tmp_path,
                dry_run=req.dry_run,
            )
        else:
            from agents.ingest import ingest_file

            raw = ingest_file(file_path=tmp_path, dry_run=req.dry_run)
            results = [{**r, "action": "create"} for r in (raw or [])]

        results = results or []
        created = sum(1 for r in results if r.get("action") == "create")
        merged = sum(1 for r in results if r.get("action") == "merge")
        skipped = sum(1 for r in results if r.get("action") == "skip")

        return {
            "url": req.url,
            "title": doc.title,
            "content_length": len(doc.content),
            "created": created,
            "merged": merged,
            "skipped": skipped,
            "entries": [
                {
                    "id": r.get("id", ""),
                    "title": r.get("title", ""),
                    "directory": r.get("target", ""),
                    "action": r.get("action", ""),
                    "novelty_score": r.get("novelty_score"),
                    "quality_score": r.get("quality_score"),
                    "reason": r.get("reason", ""),
                }
                for r in results
            ],
        }
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
