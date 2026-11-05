"""Reading endpoints for the web UI: daily briefs, the entry library and entry files.

The daily brief and entries stay Markdown in the vault; the frontend parses and renders them.
Entry summaries are cached by file mtime, so listing ~1.5k entries stays cheap on repeat calls.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import frontmatter
from fastapi import APIRouter, HTTPException, Query

from agents.utils import VAULT_ENTRY_DIRS

router = APIRouter(prefix="/library", tags=["library"])

DAILY_DIR = Path("00-Index") / "Daily"
_CALLOUT_HEAD = re.compile(r"^\[!\w+\]")
_WIKI = re.compile(r"\[\[([^\]|]+)\|?([^\]]*)\]\]")
_cache: dict[Path, tuple[float, dict[str, Any]]] = {}


def _vault() -> Path:
    from agents.config import load_config

    return load_config().vault_path


def _summary(body: str) -> str:
    """The first real paragraph, as plain text: skips headings, tables, code and callout headers."""
    for block in re.split(r"\n\s*\n", body):
        text = block.strip()
        if not text or text.startswith(("#", "|", "```", "---", "<")):
            continue
        lines = [re.sub(r"^>\s?", "", line) for line in text.splitlines()]
        text = "\n".join(line for line in lines if not _CALLOUT_HEAD.match(line.strip())).strip()
        if not text:
            continue
        text = re.sub(r"^[-*]\s*", "", text)
        text = _WIKI.sub(lambda m: m.group(2) or m.group(1), text)
        text = re.sub(r"[*`_]", "", " ".join(text.split()))
        return text[:160] + ("…" if len(text) > 160 else "")
    return ""


def _card(vault: Path, md: Path) -> dict[str, Any]:
    mtime = md.stat().st_mtime
    cached = _cache.get(md)
    if not cached or cached[0] != mtime:
        try:
            post = frontmatter.load(str(md))
            meta, body = dict(post.metadata), post.content
        except Exception:
            meta, body = {}, md.read_text(encoding="utf-8", errors="replace")
        tags = meta.get("tags") or []
        cached = (mtime, {
            "id": str(meta.get("id") or md.stem),
            "title": str(meta.get("title") or md.stem),
            "type": str(meta.get("type") or ""),
            "status": str(meta.get("status") or ""),
            "tags": [str(t) for t in tags][:6] if isinstance(tags, list) else [],
            "updated": str(meta.get("updated") or meta.get("created") or ""),
            "summary": _summary(body),
            "diagram": "```mermaid" in body,
        })
        _cache[md] = cached
    rel = md.relative_to(vault)
    return {**cached[1], "path": str(rel), "board": rel.parts[0], "mtime": mtime}


def _files(vault: Path, board: str = "") -> list[Path]:
    dirs = [board] if board else VAULT_ENTRY_DIRS
    files: list[Path] = []
    for name in dirs:
        if name not in VAULT_ENTRY_DIRS:
            continue
        folder = vault / name
        if folder.is_dir():
            files.extend(folder.rglob("*.md"))
    return files


@router.get("/overview")
def overview() -> dict[str, Any]:
    """Board counts, the id → [title, path, board] index for [[links]], and daily brief dates."""
    vault = _vault()
    counts: dict[str, int] = {}
    index: dict[str, list[str]] = {}
    for md in _files(vault):
        card = _card(vault, md)
        counts[card["board"]] = counts.get(card["board"], 0) + 1
        index[md.stem] = [card["title"], card["path"], card["board"]]
        index.setdefault(card["id"], index[md.stem])
    daily = vault / DAILY_DIR
    dailies = sorted((f.stem for f in daily.glob("*.md")), reverse=True) if daily.is_dir() else []
    return {"counts": counts, "index": index, "dailies": dailies}


@router.get("/entries")
def entries(
    board: str = "",
    q: str = "",
    limit: int = Query(60, ge=1, le=300),
) -> list[dict[str, Any]]:
    """Entries newest first, optionally one board only and files containing every word of `q`."""
    vault = _vault()
    words = [w.lower() for w in q.split() if w]
    hits = []
    for md in _files(vault, board):
        if words:
            text = md.read_text(encoding="utf-8", errors="replace").lower()
            if not all(w in text for w in words):
                continue
        hits.append(md)
    hits.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [_card(vault, md) for md in hits[:limit]]


@router.get("/file")
def read_file(path: str) -> dict[str, Any]:
    """One Markdown file inside the vault: frontmatter and body."""
    vault = _vault().resolve()
    file = (vault / path).resolve()
    if file.suffix != ".md" or not file.is_relative_to(vault) or not file.is_file():
        raise HTTPException(status_code=404, detail="没有这个文件")
    post = frontmatter.load(str(file))
    meta = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in post.metadata.items()}
    rel = str(file.relative_to(vault))
    return {"path": rel, "vault": vault.name, "meta": meta, "content": post.content}


@router.get("/daily/{date}")
def daily(date: str) -> dict[str, Any]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(status_code=400, detail="日期格式是 YYYY-MM-DD")
    return read_file(str(DAILY_DIR / f"{date}.md"))
