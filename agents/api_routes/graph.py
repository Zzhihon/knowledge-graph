"""Graph exploration and evolution tracking endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(tags=["graph"])


@router.get("/graph/links")
def get_graph_links(
    top_n: int = Query(10),
    threshold: float = Query(0.75),
) -> list[dict[str, Any]]:
    """Discover potential links between knowledge entries."""
    from agents.link import find_links

    suggestions = find_links(top_n=top_n, threshold=threshold)
    # Ensure Path objects are serialized
    return [
        {
            "source_title": s.get("source_title", ""),
            "target_title": s.get("target_title", ""),
            "similarity": s.get("similarity", 0),
            "source": s.get("source", ""),
        }
        for s in suggestions
    ]


@router.get("/graph/history/{entry_id}")
def get_graph_history(entry_id: str) -> list[dict[str, Any]]:
    """Build the supersedes chain for an entry."""
    from agents.config import load_config
    from agents.history import build_supersedes_chain, find_related_evolution
    from agents.utils import load_entries

    config = load_config()
    entries = load_entries(config.vault_path)

    chain = build_supersedes_chain(entry_id, entries)
    if chain:
        return _serialize_chain(chain, entry_id)

    # Try related evolution
    target = None
    for e in entries:
        if str(e["metadata"].get("id", "")).lower() == entry_id.lower():
            target = e
            break

    if target is None:
        return []

    related = find_related_evolution(target, entries)
    return _serialize_chain([target] + related, entry_id) if related else []


def _serialize_chain(
    chain: list[dict[str, Any]], highlight_id: str
) -> list[dict[str, Any]]:
    """Convert entry chain to JSON-safe list."""
    result = []
    for entry in chain:
        meta = entry.get("metadata", {})
        result.append({
            "id": meta.get("id", ""),
            "title": meta.get("title", ""),
            "created": meta.get("created", ""),
            "confidence": meta.get("confidence"),
            "status": meta.get("status", ""),
            "is_current": meta.get("id", "").lower() == highlight_id.lower(),
        })
    return result


@router.get("/graph/cross-domain")
def get_cross_domain(
    min_similarity: float = Query(0.6),
    max_insights: int = Query(20),
    describe: bool = Query(True),
) -> list[dict[str, Any]]:
    """Discover cross-domain knowledge connections."""
    from agents.cross_domain import discover_cross_domain

    insights = discover_cross_domain(
        min_similarity=min_similarity,
        max_insights=max_insights,
        describe=describe,
    )
    return [
        {
            "domain_a": i.domain_a,
            "domain_b": i.domain_b,
            "entry_a_id": i.entry_a_id,
            "entry_a_title": i.entry_a_title,
            "entry_b_id": i.entry_b_id,
            "entry_b_title": i.entry_b_title,
            "similarity": i.similarity,
            "description": i.description,
        }
        for i in insights
    ]


@router.get("/graph/backlinks/{entry_id}")
def get_backlinks(entry_id: str) -> list[dict[str, str]]:
    """Get all entries that reference the given entry (backlinks)."""
    from agents.backlinks import find_backlinks

    backlinks = find_backlinks(entry_id)
    return [bl.to_dict() for bl in backlinks]


@router.get("/graph/diff/{entry_id}")
def get_graph_diff(
    entry_id: str,
    limit: int = Query(10),
) -> list[dict[str, Any]]:
    """Get content evolution diff records for an entry."""
    from agents.config import load_config
    from agents.diff_store import DiffStore
    from agents.graph_store import get_graph_store

    config = load_config()
    try:
        gs = get_graph_store(config)
        gs.connect()
        try:
            ds = DiffStore(gs)
            return ds.get_history(entry_id, limit=limit)
        finally:
            gs.close()
    except Exception:
        return []
