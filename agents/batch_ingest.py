"""Batch ingestion engine for directory and multi-file processing.

Scans directories for supported files, converts them to text,
and processes them through the quality-enhanced ingestion pipeline
using a thread pool for controlled parallelism.
"""

from __future__ import annotations

import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

from agents.config import ProjectConfig, load_config
from agents.file_converter import SUPPORTED_EXTENSIONS, convert_to_text, is_supported

logger = logging.getLogger(__name__)
console = Console()


@dataclass
class FileResult:
    """Result of processing a single file."""

    file_path: str
    status: str  # "success" | "error" | "unsupported"
    entries_created: int = 0
    entries_merged: int = 0
    entries_skipped: int = 0
    entries: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class BatchResult:
    """Aggregated result of batch processing."""

    total_files: int = 0
    processed: int = 0
    entries_created: int = 0
    entries_merged: int = 0
    entries_skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    file_results: list[FileResult] = field(default_factory=list)


def ingest_directory(
    dir_path: Path,
    config: ProjectConfig | None = None,
    dry_run: bool = False,
    quality_check: bool = True,
    max_workers: int = 3,
    recursive: bool = False,
) -> BatchResult:
    """Scan a directory for supported files and ingest them.

    Args:
        dir_path: Directory to scan.
        config: Project configuration.
        dry_run: Preview without writing.
        quality_check: Enable quality assessment.
        max_workers: Thread pool size (limits Claude API concurrency).
        recursive: Scan subdirectories recursively.

    Returns:
        Aggregated BatchResult with per-file details.
    """
    if config is None:
        config = load_config()

    # Collect supported files
    if recursive:
        all_files = sorted(dir_path.rglob("*"))
    else:
        all_files = sorted(dir_path.iterdir())

    supported_files = [f for f in all_files if f.is_file() and is_supported(f)]

    if not supported_files:
        exts = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        console.print(f"[yellow]目录中未找到支持的文件 ({exts}): {dir_path}[/]")
        return BatchResult()

    console.print(
        f"[bold blue]发现 {len(supported_files)} 个待处理文件[/] "
        f"(共 {len(all_files)} 个文件)"
    )

    return _process_files(
        supported_files, config, dry_run, quality_check, max_workers
    )


def ingest_files(
    file_paths: list[Path],
    config: ProjectConfig | None = None,
    dry_run: bool = False,
    quality_check: bool = True,
    max_workers: int = 3,
) -> BatchResult:
    """Ingest a list of specific files.

    Args:
        file_paths: List of file paths to process.
        config: Project configuration.
        dry_run: Preview without writing.
        quality_check: Enable quality assessment.
        max_workers: Thread pool size.

    Returns:
        Aggregated BatchResult.
    """
    if config is None:
        config = load_config()

    return _process_files(
        file_paths, config, dry_run, quality_check, max_workers
    )


def _process_files(
    files: list[Path],
    config: ProjectConfig,
    dry_run: bool,
    quality_check: bool,
    max_workers: int,
) -> BatchResult:
    """Process files using a thread pool."""
    batch = BatchResult(total_files=len(files))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _process_single_file, f, config, dry_run, quality_check
            ): f
            for f in files
        }

        for i, future in enumerate(as_completed(futures), 1):
            file_path = futures[future]
            console.print(
                f"[dim]处理进度: {i}/{len(files)} — {file_path.name}[/]"
            )
            try:
                result = future.result()
            except Exception as exc:
                result = FileResult(
                    file_path=str(file_path),
                    status="error",
                    error=str(exc),
                )

            batch.file_results.append(result)

            if result.status == "success":
                batch.processed += 1
                batch.entries_created += result.entries_created
                batch.entries_merged += result.entries_merged
                batch.entries_skipped += result.entries_skipped
            elif result.status == "error":
                batch.errors.append({
                    "file": str(file_path),
                    "error": result.error or "未知错误",
                })
            elif result.status == "unsupported":
                pass  # Already counted in total but not processed

    return batch


def _process_single_file(
    file_path: Path,
    config: ProjectConfig,
    dry_run: bool,
    quality_check: bool,
) -> FileResult:
    """Process a single file through the ingestion pipeline.

    For non-markdown files (PDF, TXT), converts to text first,
    writes to a temporary .md file, then runs the ingest pipeline.
    """
    if not is_supported(file_path):
        return FileResult(
            file_path=str(file_path),
            status="unsupported",
        )

    try:
        # Convert to text
        text = convert_to_text(file_path)
        if not text.strip():
            return FileResult(
                file_path=str(file_path),
                status="error",
                error="文件内容为空",
            )

        # For non-markdown files, write to temp .md for the ingest pipeline
        ext = file_path.suffix.lower()
        if ext != ".md":
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".md", mode="w", encoding="utf-8"
            ) as tmp:
                tmp.write(text)
                ingest_path = Path(tmp.name)
        else:
            ingest_path = file_path

        try:
            if quality_check:
                from agents.ingest import ingest_file_with_quality

                results = ingest_file_with_quality(
                    file_path=ingest_path,
                    config=config,
                    dry_run=dry_run,
                )
            else:
                from agents.ingest import ingest_file

                raw_results = ingest_file(
                    file_path=ingest_path,
                    config=config,
                    dry_run=dry_run,
                )
                # Normalize to quality-aware format
                results = [
                    {**r, "action": "create", "novelty_score": None, "quality_score": None}
                    for r in raw_results
                ]
        finally:
            if ext != ".md":
                ingest_path.unlink(missing_ok=True)

        created = sum(1 for r in results if r.get("action") == "create")
        merged = sum(1 for r in results if r.get("action") == "merge")
        skipped = sum(1 for r in results if r.get("action") == "skip")

        return FileResult(
            file_path=str(file_path),
            status="success",
            entries_created=created,
            entries_merged=merged,
            entries_skipped=skipped,
            entries=results,
        )

    except Exception as exc:
        return FileResult(
            file_path=str(file_path),
            status="error",
            error=str(exc),
        )
