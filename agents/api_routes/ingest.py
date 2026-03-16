"""Ingest endpoint: upload and extract knowledge from files."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Query, UploadFile

router = APIRouter(tags=["ingest"])


@router.post("/ingest")
async def post_ingest(
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    quality_check: bool = Query(True),
) -> dict[str, Any]:
    """Upload a file and extract knowledge entries.

    Supports .md, .txt, and .pdf files. When quality_check is enabled,
    entries are assessed for novelty and quality before creation.
    """
    suffix = Path(file.filename or "upload.md").suffix or ".md"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        if quality_check:
            from agents.ingest import ingest_file_with_quality

            results = ingest_file_with_quality(file_path=tmp_path, dry_run=dry_run)
            created = sum(1 for r in results if r.get("action") == "create")
            merged = sum(1 for r in results if r.get("action") == "merge")
            skipped = sum(1 for r in results if r.get("action") == "skip")
            return {
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
                    for r in (results or [])
                ],
            }
        else:
            from agents.ingest import ingest_file

            results = ingest_file(file_path=tmp_path, dry_run=dry_run)
            return {
                "created": len(results) if results else 0,
                "merged": 0,
                "skipped": 0,
                "entries": [
                    {
                        "id": r.get("id", ""),
                        "title": r.get("title", ""),
                        "directory": r.get("target", ""),
                        "action": "create",
                        "novelty_score": None,
                        "quality_score": None,
                        "reason": "",
                    }
                    for r in (results or [])
                ],
            }
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/ingest/batch")
async def post_ingest_batch(
    files: list[UploadFile] = File(...),
    dry_run: bool = Query(False),
    quality_check: bool = Query(True),
) -> dict[str, Any]:
    """Upload multiple files for batch knowledge extraction.

    Supports .md, .txt, and .pdf files.
    """
    from agents.batch_ingest import ingest_files

    # Write all uploaded files to temp locations
    tmp_paths: list[Path] = []
    try:
        for f in files:
            suffix = Path(f.filename or "upload.md").suffix or ".md"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                content = await f.read()
                tmp.write(content)
                tmp_paths.append(Path(tmp.name))

        batch_result = ingest_files(
            file_paths=tmp_paths,
            dry_run=dry_run,
            quality_check=quality_check,
        )

        return {
            "total_files": batch_result.total_files,
            "processed": batch_result.processed,
            "entries_created": batch_result.entries_created,
            "entries_merged": batch_result.entries_merged,
            "entries_skipped": batch_result.entries_skipped,
            "errors": batch_result.errors,
            "file_results": [
                {
                    "file_path": fr.file_path,
                    "status": fr.status,
                    "entries_created": fr.entries_created,
                    "entries_merged": fr.entries_merged,
                    "entries_skipped": fr.entries_skipped,
                    "entries": fr.entries,
                    "error": fr.error,
                }
                for fr in batch_result.file_results
            ],
        }
    finally:
        for p in tmp_paths:
            p.unlink(missing_ok=True)
