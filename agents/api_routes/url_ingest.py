"""URL ingest endpoint: fetch a web page and extract knowledge entries via SSE."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Generator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, HttpUrl

router = APIRouter(tags=["ingest"])


class URLIngestRequest(BaseModel):
    url: HttpUrl
    dry_run: bool = False
    quality_check: bool = True
    timeout: int = 30
    tags: list[str] = Field(default_factory=list)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/ingest/url")
def ingest_url(req: URLIngestRequest) -> StreamingResponse:
    """Fetch a URL, extract content, and stream progress via SSE."""

    def event_stream() -> Generator[str, None, None]:
        from agents.sources.web import WebAdapter
        from agents.config import load_config

        url_str = str(req.url)

        yield _sse("start", {"url": url_str, "timeout": req.timeout})

        # --- Phase 1: Fetch ---
        yield _sse("phase", {"phase": "fetch", "message": "正在抓取页面..."})

        adapter = WebAdapter()
        try:
            doc = adapter.fetch_url(
                url=url_str,
                timeout=req.timeout,
                extra_tags=req.tags,
            )
        except Exception as exc:
            yield _sse("error", {"message": f"抓取失败: {exc}"})
            return

        yield _sse("fetched", {
            "title": doc.title,
            "content_length": len(doc.content),
            "author": getattr(doc, "author", ""),
        })

        # --- Phase 2: Extract ---
        yield _sse("phase", {"phase": "extract", "message": "正在提取知识条目..."})

        config = load_config()
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
                    config=config,
                    dry_run=req.dry_run,
                )
            else:
                from agents.ingest import ingest_file

                raw = ingest_file(file_path=tmp_path, config=config, dry_run=req.dry_run)
                results = [{**r, "action": "create"} for r in (raw or [])]

            results = results or []
        except Exception as exc:
            yield _sse("error", {"message": f"提取失败: {exc}"})
            return
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)

        total = len(results)
        yield _sse("extracted", {"total_entries": total})

        # --- Phase 3: Per-entry progress (quality results) ---
        if total > 0:
            yield _sse("phase", {
                "phase": "quality",
                "message": f"质量评估完成 ({total} 条)",
            })

        created = 0
        merged = 0
        skipped = 0
        entry_summaries: list[dict[str, Any]] = []

        for i, r in enumerate(results, 1):
            action = r.get("action", "create")
            if action == "create":
                created += 1
            elif action == "merge":
                merged += 1
            else:
                skipped += 1

            entry_info: dict[str, Any] = {
                "index": i,
                "total": total,
                "id": r.get("id", ""),
                "title": r.get("title", ""),
                "action": action,
                "directory": r.get("target", ""),
                "novelty_score": r.get("novelty_score"),
                "quality_score": r.get("quality_score"),
                "reason": r.get("reason", ""),
            }
            yield _sse("entry", entry_info)
            entry_summaries.append(entry_info)

        # --- Complete ---
        yield _sse("complete", {
            "url": url_str,
            "title": doc.title,
            "content_length": len(doc.content),
            "created": created,
            "merged": merged,
            "skipped": skipped,
            "entries": entry_summaries,
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
