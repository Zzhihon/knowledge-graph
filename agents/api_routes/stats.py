"""Dashboard statistics and radar endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(tags=["stats"])


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
