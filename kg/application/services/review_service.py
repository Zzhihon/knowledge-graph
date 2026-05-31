"""Compatibility surface for review behavior during the staged migration."""

from __future__ import annotations

from agents.review import (
    domain_gap_analysis,
    generate_review_report,
    print_review_summary,
    scan_for_review,
)

__all__ = [
    "domain_gap_analysis",
    "generate_review_report",
    "print_review_summary",
    "scan_for_review",
]
