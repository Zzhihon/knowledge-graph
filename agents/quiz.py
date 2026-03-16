"""Spaced repetition engine for knowledge review.

Selects entries due for review based on scheduling priority,
updates review dates according to user confidence responses,
and displays quiz questions with targeted content sections.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import frontmatter
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agents.config import load_config
from agents.utils import load_entries

console = Console()


def _parse_date(value: Any) -> date | None:
    """Parse a date from frontmatter value (string or date/datetime)."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _compute_priority(entry: dict[str, Any], today: date) -> float:
    """Compute review priority score using SM-2 variant.

    Formula:
        priority = (days_overdue * 2) + (age_days * 0.5) + ((1 - confidence) * 100)

    - days_overdue: days past review_date (0 if not yet due or no review_date)
    - age_days: days since creation (older entries accumulate priority)
    - confidence: lower confidence → higher priority

    Entries not yet due (review_date in the future) get priority 0.

    Returns:
        Continuous priority score (higher = more urgent).
    """
    meta = entry["metadata"]

    review_date = _parse_date(meta.get("review_date"))
    created_date = _parse_date(meta.get("created"))
    confidence = float(meta.get("confidence", 0.5))

    # Days overdue: positive only when review_date is past or today
    days_overdue = 0.0
    if review_date is not None:
        days_overdue = max(0.0, (today - review_date).days)
    elif created_date is not None:
        # No review_date set: treat as overdue after 7 days
        days_since_created = (today - created_date).days
        if days_since_created > 7:
            days_overdue = days_since_created - 7

    # Not due yet → skip
    if review_date is not None and review_date > today:
        return 0.0

    # Age: days since creation
    age_days = 0.0
    if created_date is not None:
        age_days = max(0.0, (today - created_date).days)

    return (days_overdue * 2) + (age_days * 0.5) + ((1 - confidence) * 100)


def select_review_entries(
    domain: str | None = None,
    count: int = 3,
) -> list[dict[str, Any]]:
    """Select entries due for review based on spaced repetition priority.

    Args:
        domain: Optional domain key to filter entries.
        count: Maximum number of entries to return.

    Returns:
        List of entry dicts sorted by review priority (highest first).
    """
    config = load_config()
    filters: dict[str, str] | None = None
    if domain:
        filters = {"domain": domain}

    entries = load_entries(config.vault_path, filters=filters)
    if not entries:
        return []

    today = date.today()
    scored: list[tuple[float, dict[str, Any]]] = []

    for entry in entries:
        priority = _compute_priority(entry, today)
        if priority > 0:
            scored.append((priority, entry))

    # Sort by priority descending, then by creation date ascending (older first)
    scored.sort(key=lambda pair: (-pair[0], str(pair[1]["metadata"].get("created", ""))))
    return [entry for _, entry in scored[:count]]


def update_review_schedule(entry_path: Path, response: str) -> None:
    """Update review scheduling based on user response.

    Reads the entry file, adjusts confidence and review_date in
    frontmatter, and writes back preserving content.

    Args:
        entry_path: Path to the markdown entry file.
        response: One of ``"confident"``, ``"partial"``, ``"forgot"``.

    Raises:
        ValueError: If response is not one of the valid options.
        FileNotFoundError: If entry_path does not exist.
    """
    valid_responses = {"confident", "partial", "forgot"}
    if response not in valid_responses:
        raise ValueError(
            f"无效的回答: '{response}'. 有效选项: {', '.join(sorted(valid_responses))}"
        )

    if not entry_path.is_file():
        raise FileNotFoundError(f"条目文件不存在: {entry_path}")

    post = frontmatter.load(str(entry_path))
    confidence = float(post.metadata.get("confidence", 0.5))
    today = date.today()

    if response == "confident":
        confidence = min(1.0, confidence + 0.05)
        next_review = today + timedelta(days=30)
    elif response == "partial":
        # confidence unchanged
        next_review = today + timedelta(days=7)
    else:  # forgot
        confidence = max(0.3, confidence - 0.1)
        next_review = today + timedelta(days=1)

    post.metadata["confidence"] = round(confidence, 2)
    post.metadata["review_date"] = next_review.isoformat()
    post.metadata["last_reviewed"] = today.isoformat()

    entry_path.write_text(frontmatter.dumps(post), encoding="utf-8")


def _extract_section(content: str, header: str) -> str | None:
    """Extract a markdown section by header name.

    Returns the section body text (without the header line), or None
    if the section is not found.
    """
    pattern = re.compile(
        rf"^##\s+{re.escape(header)}\s*\n(.*?)(?=\n##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(content)
    if match:
        return match.group(1).strip()
    return None


def print_quiz_question(entry: dict[str, Any]) -> None:
    """Display a quiz question from an entry, hiding analysis sections.

    Extracts the Question and Context sections from the entry content
    and presents them as Rich panels. Analysis and Key Insights sections
    are intentionally hidden to support active recall.

    Args:
        entry: An entry dict with 'metadata' and 'content' keys.
    """
    meta = entry["metadata"]
    content = entry["content"]
    title = meta.get("title", "未知标题")
    domain = meta.get("domain", "")
    depth = meta.get("depth", "")
    confidence = float(meta.get("confidence", 0.0))

    # Header info
    header_text = Text()
    header_text.append(f"{title}\n", style="bold white")
    header_text.append(f"域: {domain}  ", style="dim")
    header_text.append(f"深度: {depth}  ", style="dim")
    header_text.append(f"置信度: {confidence:.0%}", style="dim")

    console.print(Panel(header_text, title="复习条目", border_style="blue"))

    # Question section
    question = _extract_section(content, "Question")
    if not question:
        question = _extract_section(content, "Problem Description")
    if question:
        console.print(Panel(question, title="问题", border_style="cyan"))
    else:
        # Fall back to the first paragraph of content
        first_para = content.split("\n\n")[0].strip() if content else "（无问题内容）"
        console.print(Panel(first_para, title="问题", border_style="cyan"))

    # Context section
    context = _extract_section(content, "Context")
    if not context:
        context = _extract_section(content, "Examples")
    if context:
        console.print(Panel(context, title="上下文", border_style="dim"))

    console.print("[dim]使用 kg quiz -i 进入交互模式[/]")


def print_quiz_answer(entry: dict[str, Any]) -> None:
    """Display the answer sections of a quiz entry.

    Shows Analysis, Solution, Key Insights and other solution sections
    that were hidden during the question phase.

    Args:
        entry: An entry dict with 'metadata' and 'content' keys.
    """
    content = entry["content"]

    for header in (
        "Analysis",
        "Solution",
        "Key Insights",
        "解题思路",
        "C++ Solution",
        "Go Solution",
        "Complexity",
    ):
        section = _extract_section(content, header)
        if section:
            console.print(Panel(section, title=header, border_style="green"))
