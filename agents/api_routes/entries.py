"""Single-entry detail endpoint for the preview panel."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import frontmatter
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["entries"])

_DIRS = [
    "01-Principles", "02-Patterns", "03-Debug",
    "04-Architecture", "05-Research", "06-Team", "07-Projects",
    "08-Problems", "09-Interview",
]

# Matches the date-based prefix shared by IDs and filenames, e.g.
# "iq-20260406-hfq-07" from "iq-20260406-hfq-07-workflow-engine-details"
_PREFIX_RE = re.compile(r"^[a-z]+-\d{8}-[a-z]+-\d+")


def _build_entry_payload(vault_path: Path, md: Path) -> dict[str, Any]:
    """Load a markdown file and normalize it into the entry preview shape."""
    post = frontmatter.load(str(md))
    return {
        "metadata": dict(post.metadata),
        "content": post.content,
        "file_path": str(md),
        "relative_path": str(md.relative_to(vault_path)),
    }


def _find_entry(vault_path: Path, entry_id: str) -> dict[str, Any] | None:
    """Locate a knowledge entry by frontmatter ``id``.

    Strategy (fast → slow):
    1. Exact stem match — filename == entry_id.
    2. Substring match — entry_id appears inside the filename stem.
    3. Prefix match — the structured prefix (e.g. ``iq-20260406-hfq-07``) is
       shared between the ID and a file with a different (e.g. Chinese) suffix.
       Only the handful of prefix-matched files are opened.
    4. Full scan — brute-force fallback; parse every file's frontmatter.
    """
    # --- pass 1: exact / substring match on filename ---
    for d in _DIRS:
        folder = vault_path / d
        if not folder.is_dir():
            continue
        for md in folder.glob("*.md"):
            if md.stem == entry_id or entry_id in md.stem:
                entry = _build_entry_payload(vault_path, md)
                if entry["metadata"].get("id") == entry_id:
                    return entry

    # --- pass 2: prefix match (covers Chinese-named files) ---
    m = _PREFIX_RE.match(entry_id)
    if m:
        prefix = m.group(0)  # e.g. "iq-20260406-hfq-07"
        for d in _DIRS:
            folder = vault_path / d
            if not folder.is_dir():
                continue
            for md in folder.glob("*.md"):
                if md.stem.startswith(prefix):
                    entry = _build_entry_payload(vault_path, md)
                    if entry["metadata"].get("id") == entry_id:
                        return entry

    # --- pass 3: full scan (last resort) ---
    for d in _DIRS:
        folder = vault_path / d
        if not folder.is_dir():
            continue
        for md in folder.glob("*.md"):
            entry = _build_entry_payload(vault_path, md)
            if entry["metadata"].get("id") == entry_id:
                return entry
    return None


@router.get("/entries/{entry_id}")
def get_entry(entry_id: str) -> dict[str, Any]:
    from agents.config import load_config

    config = load_config()
    entry = _find_entry(config.vault_path, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    meta = entry["metadata"]
    return {
        "id": meta.get("id", entry_id),
        "title": meta.get("title", ""),
        "domain": meta.get("domain", ""),
        "type": meta.get("type", ""),
        "depth": meta.get("depth", ""),
        "status": meta.get("status", ""),
        "confidence": meta.get("confidence"),
        "tags": meta.get("tags", []),
        "created": meta.get("created", ""),
        "updated": meta.get("updated", ""),
        "review_date": meta.get("review_date", ""),
        "related": meta.get("related", []),
        "difficulty": meta.get("difficulty"),
        "content": entry["content"],
        "file_path": entry["file_path"],
        "relative_path": entry["relative_path"],
        "vault_name": config.vault_path.name,
    }
