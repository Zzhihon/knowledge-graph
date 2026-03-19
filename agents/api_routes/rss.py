"""RSS feed management and pull endpoints with SSE streaming progress."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Generator

import yaml
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(tags=["rss"])


class RSSPullRequest(BaseModel):
    since_days: int = 7
    workers: int = 8
    dry_run: bool = False
    quality_check: bool = True
    feeds_file: str | None = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/rss/feeds")
def list_feeds() -> dict[str, Any]:
    """List configured RSS feeds and their watermark state."""
    from agents.config import load_config
    from agents.sources.state import SourceStateManager

    config = load_config()
    feeds_path = config.vault_path / "feeds.yaml"

    if not feeds_path.exists():
        return {"feeds": [], "error": "feeds.yaml not found"}

    with open(feeds_path, encoding="utf-8") as f:
        feeds_config = yaml.safe_load(f)

    feed_list = feeds_config.get("feeds", [])
    state_manager = SourceStateManager()

    feeds_with_state = []
    for feed in feed_list:
        state = state_manager.get_state("rss", feed["url"])
        feeds_with_state.append({
            "name": feed["name"],
            "url": feed["url"],
            "domain": feed.get("domain"),
            "tags": feed.get("tags", []),
            "quality_weight": feed.get("quality_weight", 1.0),
            "last_published": state.get("last_published") if state else None,
            "last_checked": state.get("last_checked") if state else None,
        })

    return {
        "feeds": feeds_with_state,
        "config": feeds_config.get("config", {}),
    }


@router.post("/rss/pull")
def pull_rss(req: RSSPullRequest) -> StreamingResponse:
    """Pull RSS feeds with SSE streaming progress."""

    def event_stream() -> Generator[str, None, None]:
        from agents.config import load_config
        from agents.sources.rss import RSSAdapter
        from agents.sources.state import SourceStateManager
        from agents.ingest import ingest_file_with_quality, ingest_file

        config = load_config()
        feeds_path = Path(req.feeds_file) if req.feeds_file else config.vault_path / "feeds.yaml"

        if not feeds_path.exists():
            yield _sse("error", {"message": f"feeds.yaml not found: {feeds_path}"})
            return

        with open(feeds_path, encoding="utf-8") as f:
            feeds_config = yaml.safe_load(f)

        feed_list = feeds_config.get("feeds", [])
        global_config = feeds_config.get("config", {})

        if not feed_list:
            yield _sse("error", {"message": "No feeds configured"})
            return

        yield _sse("start", {
            "total_feeds": len(feed_list),
            "since_days": req.since_days,
            "workers": req.workers,
            "dry_run": req.dry_run,
        })

        # Phase 1: Fetch feeds
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=req.since_days)
        state_manager = SourceStateManager()
        all_documents = []

        yield _sse("phase", {"phase": "fetch", "message": "拉取 RSS feeds..."})

        def fetch_feed(feed_cfg):
            adapter = RSSAdapter(
                feed_url=feed_cfg["url"],
                feed_name=feed_cfg["name"],
                domain=feed_cfg.get("domain"),
                tags=feed_cfg.get("tags", []),
                quality_weight=feed_cfg.get("quality_weight", 1.0),
                state_manager=state_manager,
            )
            fetch_since = cutoff
            if fetch_since is None:
                fetch_since = adapter.get_watermark()
            docs, warning = adapter.fetch_with_status(since=fetch_since)
            if docs:
                latest = max(doc.timestamp for doc in docs)
                adapter.set_watermark(latest)
            return feed_cfg["name"], docs, warning

        with ThreadPoolExecutor(max_workers=req.workers) as executor:
            futures = {executor.submit(fetch_feed, feed): feed for feed in feed_list}
            for future in as_completed(futures):
                feed_cfg = futures[future]
                try:
                    feed_name, docs, warning = future.result()
                    all_documents.extend(docs)
                    status = "ok" if docs else ("error" if warning else "empty")
                    event: dict[str, Any] = {
                        "name": feed_name,
                        "count": len(docs),
                        "status": status,
                    }
                    if warning:
                        event["error"] = warning
                    yield _sse("feed_done", event)
                except Exception as exc:
                    yield _sse("feed_done", {
                        "name": feed_cfg["name"],
                        "count": 0,
                        "status": "error",
                        "error": str(exc),
                    })

        if not all_documents:
            yield _sse("complete", {
                "total_articles": 0,
                "created": 0, "merged": 0, "skipped": 0, "failed": 0,
            })
            return

        # Phase 2: Extract knowledge
        total = len(all_documents)
        yield _sse("phase", {
            "phase": "extract",
            "message": f"提取知识条目（{total} 篇文章）...",
            "total_articles": total,
        })

        total_created = 0
        total_merged = 0
        total_skipped = 0
        total_failed = 0

        def process_doc(args):
            idx, doc = args
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".md", delete=False, encoding="utf-8"
                ) as tmp:
                    tmp.write(doc.to_markdown())
                    tmp_path = Path(tmp.name)

                if req.quality_check:
                    results = ingest_file_with_quality(
                        file_path=tmp_path,
                        config=config,
                        dry_run=req.dry_run,
                        novelty_threshold=global_config.get("novelty_threshold", 0.3),
                        quality_threshold=global_config.get("quality_threshold", 0.4),
                    )
                else:
                    raw = ingest_file(file_path=tmp_path, config=config, dry_run=req.dry_run)
                    results = [{**r, "action": "create"} for r in raw]

                c = sum(1 for r in results if r.get("action") == "create")
                m = sum(1 for r in results if r.get("action") == "merge")
                s = sum(1 for r in results if r.get("action") == "skip")

                # Build entry summaries for frontend preview
                entries = []
                for r in results:
                    entry = {
                        "id": r.get("id", ""),
                        "title": r.get("title", ""),
                        "action": r.get("action", "create"),
                        "type": r.get("type", ""),
                        "domain": r.get("domain", ""),
                    }
                    if r.get("merge_target_id"):
                        entry["merge_target"] = r["merge_target_id"]
                    entries.append(entry)

                return idx, doc.title, c, m, s, None, entries
            except Exception as exc:
                return idx, doc.title, 0, 0, 0, str(exc), []
            finally:
                if tmp_path:
                    tmp_path.unlink(missing_ok=True)

        with ThreadPoolExecutor(max_workers=req.workers) as executor:
            futures_map = {
                executor.submit(process_doc, (i, doc)): i
                for i, doc in enumerate(all_documents, 1)
            }
            for future in as_completed(futures_map):
                idx, title, c, m, s, err, entries = future.result()
                if err is None:
                    total_created += c
                    total_merged += m
                    total_skipped += s
                    yield _sse("article_done", {
                        "index": idx, "total": total,
                        "title": title,
                        "created": c, "merged": m, "skipped": s,
                        "entries": entries,
                    })
                else:
                    total_failed += 1
                    yield _sse("article_failed", {
                        "index": idx, "total": total,
                        "title": title, "error": err,
                    })

        yield _sse("complete", {
            "total_articles": total,
            "created": total_created,
            "merged": total_merged,
            "skipped": total_skipped,
            "failed": total_failed,
            "dry_run": req.dry_run,
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/rss/state")
def get_state() -> dict[str, Any]:
    """Get watermark state for all RSS sources."""
    from agents.sources.state import SourceStateManager
    state_manager = SourceStateManager()
    return state_manager.list_sources("rss")
