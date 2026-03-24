"""Course knowledge ingestion and query utilities."""

from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.config import ProjectConfig, load_config
from agents.file_converter import convert_to_text
from agents.ingest import ingest_file, ingest_file_with_quality
from agents.utils import load_entries, slugify

COURSE_SLIDES_DIR = Path("/Users/bt1q/Bt1Q-Self/SCNU/slides")
COURSE_MODULE_TAG = "module:course-knowledge"
COURSE_SOURCE_TAG = "source:course"
COURSE_SOURCE_TYPE_TAG = "source_type:slides"
DEFAULT_WORKERS = 3
MAX_WORKERS = 6
MAX_RETRY_ATTEMPTS = 3
COURSE_PROCESS_STATE_FILE = ".kg/course_process_state.json"


@dataclass
class CourseRetryAttempt:
    attempt: int
    strategy: str
    quality_check: bool
    error: str | None = None
    success: bool = False


@dataclass
class CourseFileResult:
    file_path: str
    file_name: str
    status: str
    created: int = 0
    merged: int = 0
    skipped: int = 0
    failed: int = 0
    entries: list[dict[str, Any]] | None = None
    error: str | None = None
    retry_count: int = 0
    attempts: list[CourseRetryAttempt] | None = None


def get_course_slides_dir() -> Path:
    return COURSE_SLIDES_DIR


def get_course_file_tag(file_path: Path) -> str:
    return f"course_file:{slugify(file_path.stem)}"


def get_course_tags(file_path: Path) -> list[str]:
    return [
        COURSE_MODULE_TAG,
        COURSE_SOURCE_TAG,
        COURSE_SOURCE_TYPE_TAG,
        get_course_file_tag(file_path),
    ]


def _get_course_process_state_path(config: ProjectConfig | None = None) -> Path:
    if config is None:
        config = load_config()
    return config.root_path / COURSE_PROCESS_STATE_FILE


def load_course_process_state(config: ProjectConfig | None = None) -> dict[str, Any]:
    state_path = _get_course_process_state_path(config)
    if not state_path.is_file():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_course_process_result(
    *,
    course_file: str,
    file_name: str,
    status: str,
    error: str | None,
    retry_count: int,
    config: ProjectConfig | None = None,
) -> None:
    if config is None:
        config = load_config()
    state_path = _get_course_process_state_path(config)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = load_course_process_state(config)
    state[course_file] = {
        "file_name": file_name,
        "status": status,
        "error": error,
        "retry_count": retry_count,
        "updated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def list_course_files(config: ProjectConfig | None = None) -> dict[str, Any]:
    if config is None:
        config = load_config()
    slides_dir = get_course_slides_dir()
    exists = slides_dir.is_dir()
    files: list[dict[str, Any]] = []
    state = load_course_process_state(config)

    if exists:
        for file_path in sorted(slides_dir.glob("*.pdf")):
            stat = file_path.stat()
            course_file = slugify(file_path.stem)
            persisted = state.get(course_file, {})
            files.append({
                "file_name": file_path.name,
                "file_stem": file_path.stem,
                "file_path": str(file_path),
                "course_file": course_file,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
                "last_status": persisted.get("status", "never_run"),
                "last_error": persisted.get("error"),
                "last_retry_count": persisted.get("retry_count", 0),
                "last_processed_at": persisted.get("updated_at"),
            })

    return {
        "source_dir": str(slides_dir),
        "exists": exists,
        "total_files": len(files),
        "files": files,
    }


def _is_course_entry(metadata: dict[str, Any]) -> bool:
    tags = metadata.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    lowered = {str(tag).lower() for tag in tags}
    return COURSE_MODULE_TAG.lower() in lowered and COURSE_SOURCE_TAG.lower() in lowered


def _matches_optional_tag(tags: list[str], expected: str | None) -> bool:
    if not expected:
        return True
    lowered = {str(tag).lower() for tag in tags}
    return expected.lower() in lowered


def _matches_search(metadata: dict[str, Any], content: str, search: str | None) -> bool:
    if not search:
        return True
    q = search.lower()
    title = str(metadata.get("title", "")).lower()
    domain = str(metadata.get("domain", "")).lower()
    sub_domain = str(metadata.get("sub_domain", "")).lower()
    tags = " ".join(str(t) for t in metadata.get("tags", []))
    haystack = "\n".join([title, domain, sub_domain, tags.lower(), content.lower()])
    return q in haystack


def list_course_entries(
    *,
    course_file: str | None = None,
    tag: str | None = None,
    domain: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
    config: ProjectConfig | None = None,
) -> dict[str, Any]:
    if config is None:
        config = load_config()

    entries = load_entries(config.vault_path)
    filtered: list[dict[str, Any]] = []

    for entry in entries:
        metadata = entry["metadata"]
        if not _is_course_entry(metadata):
            continue

        tags = metadata.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]

        if course_file and not _matches_optional_tag(tags, f"course_file:{course_file}"):
            continue
        if tag and not _matches_optional_tag(tags, tag):
            continue
        if domain and str(metadata.get("domain", "")).lower() != domain.lower():
            continue
        if not _matches_search(metadata, entry["content"], search):
            continue

        filtered.append({
            "id": metadata.get("id", ""),
            "title": metadata.get("title", ""),
            "domain": metadata.get("domain", ""),
            "type": metadata.get("type", ""),
            "depth": metadata.get("depth", ""),
            "status": metadata.get("status", ""),
            "confidence": metadata.get("confidence"),
            "tags": tags,
            "created": metadata.get("created", ""),
            "updated": metadata.get("updated", ""),
            "course_file": next((str(t).split(":", 1)[1] for t in tags if str(t).startswith("course_file:")), ""),
            "file_path": str(entry["path"]),
        })

    filtered.sort(key=lambda item: (item["updated"], item["created"], item["title"]), reverse=True)

    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    items = filtered[start:end]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if page_size else 1,
    }


def get_course_stats(config: ProjectConfig | None = None) -> dict[str, Any]:
    if config is None:
        config = load_config()

    entries = load_entries(config.vault_path)
    course_entries = [entry for entry in entries if _is_course_entry(entry["metadata"])]
    file_index = list_course_files()

    type_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    course_file_counts: dict[str, int] = {}
    total_confidence = 0.0
    confidence_count = 0

    for entry in course_entries:
        metadata = entry["metadata"]
        entry_type = str(metadata.get("type", "unknown"))
        type_counts[entry_type] = type_counts.get(entry_type, 0) + 1

        domain = str(metadata.get("domain", "unknown"))
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

        tags = metadata.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        for tag in tags:
            tag_str = str(tag)
            if tag_str.startswith("course_file:"):
                key = tag_str.split(":", 1)[1]
                course_file_counts[key] = course_file_counts.get(key, 0) + 1

        confidence = metadata.get("confidence")
        if isinstance(confidence, (int, float)):
            total_confidence += float(confidence)
            confidence_count += 1

    persisted_state = load_course_process_state(config)
    failed_files = [key for key, value in persisted_state.items() if value.get("status") == "failed"]

    return {
        "source_dir": file_index["source_dir"],
        "total_files": file_index["total_files"],
        "total_entries": len(course_entries),
        "avg_confidence": (total_confidence / confidence_count) if confidence_count else None,
        "type_counts": type_counts,
        "domain_counts": domain_counts,
        "course_file_counts": course_file_counts,
        "failed_course_files": failed_files,
    }


def _is_retryable_error(error: str) -> bool:
    lowered = error.lower()
    retry_markers = [
        "timeout",
        "timed out",
        "api 调用失败",
        "rate limit",
        "429",
        "connection",
        "network",
        "json",
        "parse",
        "max_tokens",
        "truncated",
        "stream",
        "temporarily",
    ]
    return any(marker in lowered for marker in retry_markers)


def _run_ingest_once(
    file_path: Path,
    *,
    config: ProjectConfig,
    dry_run: bool,
    quality_check: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tmp_path: Path | None = None
    try:
        text = convert_to_text(file_path)
        if not text.strip():
            raise ValueError("文件内容为空")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".md", mode="w", encoding="utf-8") as tmp:
            tmp.write(text)
            tmp_path = Path(tmp.name)

        extra_tags = get_course_tags(file_path)
        if quality_check:
            results = ingest_file_with_quality(
                file_path=tmp_path,
                config=config,
                dry_run=dry_run,
                extra_tags=extra_tags,
            )
        else:
            raw_results = ingest_file(
                file_path=tmp_path,
                config=config,
                dry_run=dry_run,
            )
            results = []
            for item in raw_results:
                results.append({
                    **item,
                    "action": "create",
                    "status": item.get("status", "created"),
                })

        normalized_entries = []
        for item in results:
            normalized_entries.append({
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "action": item.get("action", "create"),
                "type": item.get("type", ""),
                "domain": item.get("domain", ""),
                "course_file": slugify(file_path.stem),
                "tags": get_course_tags(file_path),
                "merge_target": item.get("merge_target_id"),
            })

        return results, normalized_entries
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def process_course_file(
    file_path: Path,
    *,
    config: ProjectConfig,
    dry_run: bool,
    quality_check: bool,
) -> CourseFileResult:
    attempts: list[CourseRetryAttempt] = []
    strategies: list[tuple[str, bool]] = [("default", quality_check)]

    if quality_check:
        strategies.append(("retry_same_strategy", True))
        strategies.append(("fallback_without_quality_check", False))
    else:
        strategies.append(("retry_same_strategy", False))

    last_error: str | None = None

    for index, (strategy, current_quality_check) in enumerate(strategies, start=1):
        if index > MAX_RETRY_ATTEMPTS:
            break
        try:
            results, normalized_entries = _run_ingest_once(
                file_path,
                config=config,
                dry_run=dry_run,
                quality_check=current_quality_check,
            )

            created = sum(1 for item in results if item.get("action") == "create")
            merged = sum(1 for item in results if item.get("action") == "merge")
            skipped = sum(1 for item in results if item.get("action") == "skip")

            attempts.append(CourseRetryAttempt(
                attempt=index,
                strategy=strategy,
                quality_check=current_quality_check,
                success=True,
            ))

            return CourseFileResult(
                file_path=str(file_path),
                file_name=file_path.name,
                status="success",
                created=created,
                merged=merged,
                skipped=skipped,
                failed=0,
                entries=normalized_entries,
                retry_count=max(0, index - 1),
                attempts=attempts,
            )
        except Exception as exc:
            last_error = str(exc)
            attempts.append(CourseRetryAttempt(
                attempt=index,
                strategy=strategy,
                quality_check=current_quality_check,
                error=last_error,
                success=False,
            ))

            is_last_strategy = index == len(strategies) or index == MAX_RETRY_ATTEMPTS
            if is_last_strategy:
                break

            if strategy == "default" and not _is_retryable_error(last_error):
                break

            time.sleep(min(index, 2))

    return CourseFileResult(
        file_path=str(file_path),
        file_name=file_path.name,
        status="error",
        failed=1,
        error=last_error or "未知错误",
        retry_count=max(0, len(attempts) - 1),
        attempts=attempts,
    )


def iter_course_processing(
    *,
    workers: int = DEFAULT_WORKERS,
    dry_run: bool = False,
    quality_check: bool = True,
    course_files: list[str] | None = None,
    config: ProjectConfig | None = None,
):
    if config is None:
        config = load_config()

    file_index = list_course_files()
    requested = {item.lower() for item in (course_files or []) if item}
    indexed_files = file_index["files"]
    if requested:
        indexed_files = [item for item in indexed_files if str(item["course_file"]).lower() in requested]
    files = [Path(item["file_path"]) for item in indexed_files]
    worker_count = max(1, min(workers, MAX_WORKERS))

    yield "start", {
        "source_dir": file_index["source_dir"],
        "total_files": len(files),
        "workers": worker_count,
        "dry_run": dry_run,
        "quality_check": quality_check,
        "course_files": sorted(requested) if requested else None,
    }

    if not file_index["exists"]:
        yield "error", {"message": f"课程目录不存在: {file_index['source_dir']}"}
        return

    if requested and not files:
        yield "error", {"message": "未找到指定的失败课件文件"}
        return

    if not files:
        yield "complete", {
            "source_dir": file_index["source_dir"],
            "total_files": 0,
            "processed": 0,
            "created": 0,
            "merged": 0,
            "skipped": 0,
            "failed": 0,
            "dry_run": dry_run,
        }
        return

    yield "phase", {
        "phase": "process",
        "message": f"并发处理课程 PDF（{len(files)} 个文件）",
        "total_files": len(files),
    }

    processed = 0
    total_created = 0
    total_merged = 0
    total_skipped = 0
    total_failed = 0

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                process_course_file,
                file_path,
                config=config,
                dry_run=dry_run,
                quality_check=quality_check,
            ): file_path
            for file_path in files
        }

        for future in as_completed(futures):
            file_path = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = CourseFileResult(
                    file_path=str(file_path),
                    file_name=file_path.name,
                    status="error",
                    failed=1,
                    error=str(exc),
                )

            processed += 1
            if result.attempts:
                for attempt in result.attempts[1:]:
                    yield "file_retry", {
                        "index": processed,
                        "total": len(files),
                        "file_name": result.file_name,
                        "file_path": result.file_path,
                        "attempt": attempt.attempt,
                        "strategy": attempt.strategy,
                        "quality_check": attempt.quality_check,
                        "error": attempt.error,
                        "success": attempt.success,
                    }

            course_file_slug = slugify(Path(result.file_name).stem)
            save_course_process_result(
                course_file=course_file_slug,
                file_name=result.file_name,
                status="failed" if result.status != "success" else "success",
                error=result.error,
                retry_count=result.retry_count,
                config=config,
            )

            if result.status == "success":
                total_created += result.created
                total_merged += result.merged
                total_skipped += result.skipped
                yield "file_done", {
                    "index": processed,
                    "total": len(files),
                    "file_name": result.file_name,
                    "file_path": result.file_path,
                    "course_file": course_file_slug,
                    "created": result.created,
                    "merged": result.merged,
                    "skipped": result.skipped,
                    "entries": result.entries or [],
                    "retry_count": result.retry_count,
                    "attempts": [
                        {
                            "attempt": a.attempt,
                            "strategy": a.strategy,
                            "quality_check": a.quality_check,
                            "error": a.error,
                            "success": a.success,
                        }
                        for a in (result.attempts or [])
                    ],
                }
            else:
                total_failed += 1
                yield "file_failed", {
                    "index": processed,
                    "total": len(files),
                    "file_name": result.file_name,
                    "file_path": result.file_path,
                    "course_file": course_file_slug,
                    "error": result.error or "未知错误",
                    "retry_count": result.retry_count,
                    "attempts": [
                        {
                            "attempt": a.attempt,
                            "strategy": a.strategy,
                            "quality_check": a.quality_check,
                            "error": a.error,
                            "success": a.success,
                        }
                        for a in (result.attempts or [])
                    ],
                }

    yield "complete", {
        "source_dir": file_index["source_dir"],
        "total_files": len(files),
        "processed": processed,
        "created": total_created,
        "merged": total_merged,
        "skipped": total_skipped,
        "failed": total_failed,
        "dry_run": dry_run,
    }
