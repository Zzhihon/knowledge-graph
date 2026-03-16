"""Spaced repetition quiz endpoints."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(tags=["quiz"])


def _serialize_quiz_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert a quiz entry to a JSON-safe structure with question/answer split."""
    meta = entry.get("metadata", {})
    content = entry.get("content", "")

    # Split content into question/context and answer by markdown heading
    question = ""
    context = ""
    answer = ""

    sections = re.split(r"^(##\s+.+)$", content, flags=re.MULTILINE)
    current_section = "preamble"
    preamble_parts: list[str] = []
    answer_parts: list[str] = []

    for part in sections:
        heading_match = re.match(r"^##\s+(.+)$", part.strip())
        if heading_match:
            heading = heading_match.group(1).strip().lower()
            if any(k in heading for k in ("答案", "answer", "解答", "解题", "solution")):
                current_section = "answer"
            else:
                current_section = "preamble"
            continue
        if current_section == "answer":
            answer_parts.append(part.strip())
        else:
            preamble_parts.append(part.strip())

    question_text = "\n".join(p for p in preamble_parts if p)
    answer_text = "\n".join(p for p in answer_parts if p)

    # If no explicit answer section, use the full content as context
    if not answer_text:
        context = question_text
        question = meta.get("title", "")
        answer = question_text
    else:
        question = question_text or meta.get("title", "")
        answer = answer_text

    file_path = meta.get("file_path", "") or str(entry.get("path", ""))

    return {
        "id": meta.get("id", ""),
        "title": meta.get("title", ""),
        "question": question,
        "context": context,
        "answer": answer,
        "tags": meta.get("tags", []),
        "layer": meta.get("depth", ""),
        "confidence": meta.get("confidence"),
        "file_path": str(file_path),
    }


class ScoreRequest(BaseModel):
    file_path: str
    response: str  # "confident" | "partial" | "forgot"


@router.get("/quiz/entries")
def get_quiz_entries(
    domain: str | None = Query(None),
    count: int = Query(5),
) -> list[dict[str, Any]]:
    """Select entries due for spaced repetition review."""
    from agents.quiz import select_review_entries

    entries = select_review_entries(domain=domain, count=count)
    return [_serialize_quiz_entry(e) for e in entries]


@router.post("/quiz/score")
def post_quiz_score(req: ScoreRequest) -> dict[str, Any]:
    """Record a self-assessment score and update frontmatter."""
    import frontmatter

    from agents.quiz import update_review_schedule

    entry_path = Path(req.file_path)
    update_review_schedule(entry_path, req.response)

    # Read back updated metadata
    new_confidence = None
    next_review = None
    if entry_path.exists():
        post = frontmatter.load(str(entry_path))
        new_confidence = post.metadata.get("confidence")
        next_review = str(post.metadata.get("review_date", ""))

    return {
        "ok": True,
        "new_confidence": new_confidence,
        "next_review": next_review,
    }
