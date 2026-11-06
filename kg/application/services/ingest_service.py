"""Compatibility surface for ingest behavior during the staged migration."""

from __future__ import annotations

from agents.ingest import ingest_file, ingest_file_with_quality

__all__ = ["ingest_file", "ingest_file_with_quality"]
