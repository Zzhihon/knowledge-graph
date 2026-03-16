"""Problem bank and exam generation endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(tags=["problems"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class GeneratePatternRequest(BaseModel):
    pattern_name: str
    chinese_name: str | None = None
    problem_count: int = 5
    dry_run: bool = False


class GenerateExamRequest(BaseModel):
    problem_count: int = 4
    difficulty_distribution: dict[str, int] | None = None
    patterns: list[str] | None = None
    exclude_recently_reviewed: bool = True


# ---------------------------------------------------------------------------
# GET /problems — paginated problem list with filters
# ---------------------------------------------------------------------------

@router.get("/problems")
def list_problems(
    pattern: str | None = Query(None),
    difficulty: str | None = Query(None),
    company: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """List problem entries with optional filtering and pagination."""
    from agents.config import load_config
    from agents.utils import load_entries

    config = load_config()
    entries = load_entries(config.vault_path, filters={"type": "problem"})

    # Apply filters
    filtered = []
    for entry in entries:
        meta = entry["metadata"]

        if pattern:
            entry_patterns = meta.get("pattern", [])
            if isinstance(entry_patterns, str):
                entry_patterns = [entry_patterns]
            if pattern not in entry_patterns:
                continue

        if difficulty:
            if str(meta.get("difficulty", "")).lower() != difficulty.lower():
                continue

        if company:
            companies = meta.get("companies", [])
            if isinstance(companies, list):
                if not any(company.lower() in c.lower() for c in companies):
                    continue
            else:
                continue

        if search:
            title = str(meta.get("title", "")).lower()
            tags = " ".join(str(t) for t in meta.get("tags", []))
            if search.lower() not in title and search.lower() not in tags.lower():
                continue

        filtered.append(entry)

    # Sort by leetcode_id if available, then by title
    filtered.sort(key=lambda e: (
        e["metadata"].get("leetcode_id") or 9999,
        e["metadata"].get("title", ""),
    ))

    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = filtered[start:end]

    items = []
    for entry in page_items:
        meta = entry["metadata"]
        items.append({
            "id": meta.get("id", ""),
            "title": meta.get("title", ""),
            "leetcode_id": meta.get("leetcode_id"),
            "difficulty": meta.get("difficulty", ""),
            "pattern": meta.get("pattern", []),
            "companies": meta.get("companies", []),
            "confidence": meta.get("confidence"),
            "tags": meta.get("tags", []),
            "review_date": meta.get("review_date", ""),
            "file_path": str(entry["path"]),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if page_size else 1,
    }


# ---------------------------------------------------------------------------
# GET /problems/patterns — pattern overview with stats
# ---------------------------------------------------------------------------

@router.get("/problems/patterns")
def list_patterns() -> list[dict[str, Any]]:
    """List all known patterns with active/pending status and problem counts."""
    from agents.config import load_config
    from agents.utils import load_entries
    from agents.problem_generator import get_available_patterns

    config = load_config()
    pattern_info = get_available_patterns()

    # Count problems per pattern
    entries = load_entries(config.vault_path, filters={"type": "problem"})
    pattern_counts: dict[str, int] = {}
    for entry in entries:
        meta = entry["metadata"]
        pats = meta.get("pattern", [])
        if isinstance(pats, str):
            pats = [pats]
        for p in pats:
            pattern_counts[p] = pattern_counts.get(p, 0) + 1

    result = []
    for name, info in sorted(pattern_info.items()):
        result.append({
            "name": name,
            "chinese_name": info["chinese_name"],
            "status": info["status"],
            "problem_count": pattern_counts.get(name, 0),
            "anchors": info.get("anchors", []),
        })

    return result


# ---------------------------------------------------------------------------
# GET /problems/stats — aggregate statistics
# ---------------------------------------------------------------------------

@router.get("/problems/stats")
def get_problem_stats() -> dict[str, Any]:
    """Return aggregate statistics about the problem bank."""
    from agents.config import load_config
    from agents.utils import load_entries
    from agents.problem_generator import get_available_patterns
    from datetime import datetime, timezone

    config = load_config()
    entries = load_entries(config.vault_path, filters={"type": "problem"})

    total = len(entries)
    difficulty_dist: dict[str, int] = {}
    pattern_set: set[str] = set()
    needs_review = 0
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    for entry in entries:
        meta = entry["metadata"]

        diff = str(meta.get("difficulty", "medium")).lower()
        difficulty_dist[diff] = difficulty_dist.get(diff, 0) + 1

        pats = meta.get("pattern", [])
        if isinstance(pats, str):
            pats = [pats]
        pattern_set.update(pats)

        review_date = str(meta.get("review_date", ""))
        if review_date and review_date <= today:
            needs_review += 1

    pattern_info = get_available_patterns()
    active_count = sum(1 for v in pattern_info.values() if v["status"] == "active")
    total_patterns = len(pattern_info)

    return {
        "total_problems": total,
        "difficulty_distribution": difficulty_dist,
        "pattern_coverage": f"{active_count}/{total_patterns}",
        "active_patterns": active_count,
        "total_patterns": total_patterns,
        "covered_patterns": sorted(pattern_set),
        "needs_review": needs_review,
    }


# ---------------------------------------------------------------------------
# POST /problems/generate-pattern — generate a new pattern batch
# ---------------------------------------------------------------------------

@router.post("/problems/generate-pattern")
def post_generate_pattern(body: GeneratePatternRequest) -> dict[str, Any]:
    """Generate a pattern template + problem entries via Claude API."""
    from agents.problem_generator import generate_pattern_batch

    try:
        result = generate_pattern_batch(
            pattern_name=body.pattern_name,
            chinese_name=body.chinese_name,
            problem_count=body.problem_count,
            dry_run=body.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "pattern_name": result.pattern_name,
        "pattern_file": result.pattern_file,
        "problems": [
            {
                "entry_id": p.entry_id,
                "title": p.title,
                "leetcode_id": p.leetcode_id,
                "difficulty": p.difficulty,
                "file_path": p.file_path,
            }
            for p in result.problems
        ],
        "errors": result.errors,
    }


# ---------------------------------------------------------------------------
# POST /problems/generate-exam — generate a mock interview paper
# ---------------------------------------------------------------------------

@router.post("/problems/generate-exam")
def post_generate_exam(body: GenerateExamRequest) -> dict[str, Any]:
    """Generate a mock interview exam paper from existing problems."""
    from agents.exam_generator import generate_exam

    try:
        exam = generate_exam(
            problem_count=body.problem_count,
            difficulty_distribution=body.difficulty_distribution,
            patterns=body.patterns,
            exclude_recently_reviewed=body.exclude_recently_reviewed,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "problems": [
            {
                "id": p.id,
                "title": p.title,
                "leetcode_id": p.leetcode_id,
                "difficulty": p.difficulty,
                "pattern": p.pattern,
                "companies": p.companies,
                "time_estimate": p.time_estimate,
                "content": p.content,
                "file_path": p.file_path,
            }
            for p in exam.problems
        ],
        "total_time": exam.total_time,
        "difficulty_distribution": exam.difficulty_distribution,
        "pattern_coverage": exam.pattern_coverage,
    }
