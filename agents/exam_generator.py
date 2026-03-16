"""Mock interview exam paper generator.

Assembles exam papers from existing problem entries without calling
any external API.  Supports difficulty distribution, pattern diversity,
and optional filtering by review recency.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from agents.config import load_config, ProjectConfig
from agents.utils import load_entries

# Time estimates per difficulty level (minutes)
_TIME_ESTIMATES: dict[str, int] = {
    "easy": 10,
    "medium": 20,
    "hard": 30,
}

_DEFAULT_DIFFICULTY_DIST: dict[str, int] = {
    "easy": 1,
    "medium": 2,
    "hard": 1,
}


@dataclass
class ExamProblem:
    """A single problem in an exam paper."""
    id: str
    title: str
    leetcode_id: int | None
    difficulty: str
    pattern: list[str]
    companies: list[str]
    time_estimate: int
    content: str
    file_path: str


@dataclass
class ExamPaper:
    """A complete exam paper."""
    problems: list[ExamProblem] = field(default_factory=list)
    total_time: int = 0
    difficulty_distribution: dict[str, int] = field(default_factory=dict)
    pattern_coverage: list[str] = field(default_factory=list)


def generate_exam(
    problem_count: int = 4,
    difficulty_distribution: dict[str, int] | None = None,
    patterns: list[str] | None = None,
    exclude_recently_reviewed: bool = True,
    config: ProjectConfig | None = None,
) -> ExamPaper:
    """Generate a mock interview exam paper from existing entries.

    Args:
        problem_count: Total problems in the exam.
        difficulty_distribution: e.g. {"easy": 1, "medium": 2, "hard": 1}.
            Defaults to 1E+2M+1H.
        patterns: If given, only select from these patterns.
        exclude_recently_reviewed: Skip problems reviewed in last 3 days.
        config: Project config. Auto-loaded if None.

    Returns:
        ExamPaper with selected problems and metadata.
    """
    if config is None:
        config = load_config()

    if difficulty_distribution is None:
        difficulty_distribution = dict(_DEFAULT_DIFFICULTY_DIST)

    # Load all problem entries
    all_problems = load_entries(config.vault_path, filters={"type": "problem"})

    # Apply pattern filter
    if patterns:
        pattern_set = set(patterns)
        all_problems = [
            e for e in all_problems
            if _has_pattern_overlap(e["metadata"], pattern_set)
        ]

    # Apply recently-reviewed filter
    if exclude_recently_reviewed:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=3)
        all_problems = [
            e for e in all_problems
            if not _was_reviewed_after(e["metadata"], cutoff)
        ]

    # Group by difficulty
    by_difficulty: dict[str, list[dict[str, Any]]] = {
        "easy": [], "medium": [], "hard": [],
    }
    for entry in all_problems:
        diff = str(entry["metadata"].get("difficulty", "medium")).lower()
        if diff in by_difficulty:
            by_difficulty[diff].append(entry)

    # Select problems according to difficulty distribution
    selected: list[dict[str, Any]] = []
    used_patterns: set[str] = set()

    for diff, count in difficulty_distribution.items():
        pool = by_difficulty.get(diff, [])
        random.shuffle(pool)

        picked = 0
        for entry in pool:
            if picked >= count:
                break
            # Prefer pattern diversity: skip if primary pattern already used
            entry_patterns = _get_patterns(entry["metadata"])
            primary = entry_patterns[0] if entry_patterns else None
            if primary and primary in used_patterns and len(pool) > count:
                continue
            selected.append(entry)
            if primary:
                used_patterns.add(primary)
            picked += 1

        # If not enough problems with diversity constraint, fill remaining
        if picked < count:
            remaining = [e for e in pool if e not in selected]
            for entry in remaining[:count - picked]:
                selected.append(entry)

    # If total selected < problem_count, fill from any remaining
    if len(selected) < problem_count:
        used_ids = {e["metadata"].get("id") for e in selected}
        remaining = [e for e in all_problems if e["metadata"].get("id") not in used_ids]
        random.shuffle(remaining)
        selected.extend(remaining[:problem_count - len(selected)])

    # Shuffle final order
    random.shuffle(selected)
    selected = selected[:problem_count]

    # Build ExamPaper
    exam = ExamPaper()
    pattern_set: set[str] = set()

    for entry in selected:
        meta = entry["metadata"]
        difficulty = str(meta.get("difficulty", "medium")).lower()
        entry_patterns = _get_patterns(meta)
        companies_raw = meta.get("companies", [])
        companies = companies_raw if isinstance(companies_raw, list) else []

        problem = ExamProblem(
            id=meta.get("id", ""),
            title=meta.get("title", ""),
            leetcode_id=meta.get("leetcode_id"),
            difficulty=difficulty,
            pattern=entry_patterns,
            companies=companies,
            time_estimate=_TIME_ESTIMATES.get(difficulty, 20),
            content=entry["content"],
            file_path=str(entry["path"]),
        )
        exam.problems.append(problem)
        exam.total_time += problem.time_estimate
        pattern_set.update(entry_patterns)

    # Compile stats
    exam.pattern_coverage = sorted(pattern_set)
    diff_counts: dict[str, int] = {}
    for p in exam.problems:
        diff_counts[p.difficulty] = diff_counts.get(p.difficulty, 0) + 1
    exam.difficulty_distribution = diff_counts

    return exam


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_patterns(metadata: dict[str, Any]) -> list[str]:
    """Extract pattern list from entry metadata."""
    pat = metadata.get("pattern", [])
    if isinstance(pat, str):
        return [pat]
    if isinstance(pat, list):
        return [str(p) for p in pat]
    return []


def _has_pattern_overlap(metadata: dict[str, Any], pattern_set: set[str]) -> bool:
    """Check if entry has any pattern from the given set."""
    return bool(set(_get_patterns(metadata)) & pattern_set)


def _was_reviewed_after(metadata: dict[str, Any], cutoff: datetime) -> bool:
    """Check if the entry was reviewed after the cutoff date."""
    review_str = metadata.get("review_date", "")
    if not review_str:
        return False
    try:
        review_date = datetime.fromisoformat(str(review_str).replace("Z", "+00:00"))
        if review_date.tzinfo is None:
            review_date = review_date.replace(tzinfo=timezone.utc)
        return review_date > cutoff
    except (ValueError, TypeError):
        return False
