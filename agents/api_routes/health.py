"""Knowledge health inspection endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(tags=["health"])


@router.get("/health/review")
def get_health_review(domain: str | None = Query(None)) -> dict[str, Any]:
    """Scan for entries needing attention: outdated, low-confidence, drafts."""
    from agents.review import scan_for_review

    result = scan_for_review(domain_filter=domain)

    # Serialize Path objects and simplify for JSON
    for category in ("outdated", "low_confidence", "drafts"):
        items = result.get(category, [])
        serialized: list[dict[str, Any]] = []
        for item in items:
            meta = item.get("metadata", {})
            serialized.append({
                "id": meta.get("id", ""),
                "title": meta.get("title", ""),
                "last_updated": meta.get("last_updated", meta.get("created", "")),
                "confidence": meta.get("confidence"),
                "status": meta.get("status", ""),
                "domain": meta.get("domain", ""),
                "file_path": str(meta.get("file_path", item.get("path", ""))),
            })
        result[category] = serialized

    return result


@router.get("/health/gaps")
def get_health_gaps() -> dict[str, Any]:
    """Domain gap analysis: coverage and missing sub-domains."""
    from agents.review import domain_gap_analysis

    return domain_gap_analysis()
