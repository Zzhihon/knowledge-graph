"""Single-entry detail endpoint for the preview panel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["entries"])


def _find_entry(vault_path: Path, entry_id: str) -> dict[str, Any] | None:
    """Locate a knowledge entry by ID without loading every file."""
    dirs = [
        "01-Principles", "02-Patterns", "03-Debug",
        "04-Architecture", "05-Research", "06-Team", "08-Problems",
    ]
    for d in dirs:
        folder = vault_path / d
        if not folder.is_dir():
            continue
        for md in folder.glob("*.md"):
            if entry_id in md.stem:
                post = frontmatter.load(str(md))
                if post.metadata.get("id") == entry_id:
                    return {
                        "metadata": dict(post.metadata),
                        "content": post.content,
                        "file_path": str(md),
                        "relative_path": str(md.relative_to(vault_path)),
                    }
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
