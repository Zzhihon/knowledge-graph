"""Compatibility surface for batch ingest during the staged migration."""

from __future__ import annotations

from agents.batch_ingest import BatchResult, FileResult, ingest_directory, ingest_files

__all__ = ["BatchResult", "FileResult", "ingest_directory", "ingest_files"]
