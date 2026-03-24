"""Dashboard statistics and radar endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(tags=["stats"])


@router.get("/recent-feed")
def get_recent_feed(limit: int = Query(30, ge=1, le=100)) -> dict[str, Any]:
    """Return recently created entries for the dashboard carousel.

    Prefers RSS-sourced entries (tagged source:rss), but falls back to
    all recent entries so the carousel is never empty.
    """
    from agents.config import load_config
    from agents.utils import load_entries

    config = load_config()
    entries = load_entries(config.vault_path)

    rss_items: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []

    for entry in entries:
        meta = entry["metadata"]
        tags = meta.get("tags", [])
        if not isinstance(tags, list):
            tags = [tags]

        # Extract feed name from feed:xxx tag
        feed_name = ""
        for t in tags:
            if isinstance(t, str) and t.startswith("feed:"):
                feed_name = t[5:]
                break

        # Derive source label from source.type metadata
        source_meta = meta.get("source", {})
        source_label = ""
        if isinstance(source_meta, dict):
            source_label = source_meta.get("type", "")
        elif isinstance(source_meta, str):
            source_label = source_meta

        domain = meta.get("domain", "unknown")
        if isinstance(domain, list):
            domain = domain[:3]

        item = {
            "id": meta.get("id", ""),
            "title": meta.get("title", ""),
            "domain": domain,
            "created": meta.get("created", ""),
            "feed_name": feed_name or source_label,
            "type": meta.get("type", ""),
        }

        if "source:rss" in tags:
            rss_items.append(item)
        all_items.append(item)

    # Prefer RSS entries; fall back to all entries when none exist
    result = rss_items if rss_items else all_items

    # Sort by created date descending
    result.sort(key=lambda x: x["created"], reverse=True)
    result = result[:limit]

    return {"items": result, "total": len(result)}


@router.get("/stats")
def get_stats() -> dict[str, Any]:
    """Aggregate vault statistics for the dashboard."""
    from agents.config import load_config
    from agents.quiz import select_review_entries
    from agents.utils import load_entries

    config = load_config()
    entries = load_entries(config.vault_path)

    domain_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    layer_counts: dict[str, int] = {"principles": 0, "patterns": 0, "problems": 0}
    total_confidence = 0.0
    confidence_count = 0

    for entry in entries:
        meta = entry["metadata"]

        # Domain counts
        d = meta.get("domain", "unknown")
        domains = d if isinstance(d, list) else [d]
        for dk in domains:
            domain_counts[dk] = domain_counts.get(dk, 0) + 1

        # Type counts
        t = meta.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

        # Layer counts based on entry type (maps to vault directory structure)
        etype = meta.get("type", "")
        if etype in ("principle",):
            layer_counts["principles"] += 1
        elif etype in ("pattern",):
            layer_counts["patterns"] += 1
        elif etype in ("problem", "debug"):
            layer_counts["problems"] += 1

        # Confidence average
        conf = meta.get("confidence")
        if conf is not None:
            try:
                total_confidence += float(conf)
                confidence_count += 1
            except (ValueError, TypeError):
                pass

    # Count entries needing review
    try:
        review_entries = select_review_entries(domain=None, count=100)
        needs_review = len(review_entries)
    except Exception:
        needs_review = 0

    avg_confidence = total_confidence / confidence_count if confidence_count else 0.0

    return {
        "total_items": len(entries),
        "needs_review": needs_review,
        "avg_confidence": round(avg_confidence, 2),
        "domains": list(domain_counts.keys()),
        "type_counts": type_counts,
        "layer_counts": layer_counts,
    }


@router.get("/radar")
def get_radar(domain: str | None = Query(None)) -> dict[str, Any]:
    """Compute domain strength metrics for the radar chart."""
    from agents.radar import compute_all_strengths, compute_domain_strength

    if domain:
        from agents.config import load_config
        from agents.utils import load_entries

        config = load_config()
        entries = load_entries(config.vault_path)
        strength = compute_domain_strength(domain, entries, config)
        return {domain: strength}

    return compute_all_strengths()
