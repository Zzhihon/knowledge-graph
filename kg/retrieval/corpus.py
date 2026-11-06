"""Vault entries as index input: which files exist, and how one entry becomes chunks.

By default a chunk is a whole entry (first ENTRY_CHARS of the body); with granularity
"section" it is one section of an entry (split on Markdown headings outside code fences). The text
that is embedded and BM25-indexed starts with the entry title, tags and heading path, so a
section that only says "改成两阶段提交" still knows which entry and topic it belongs to.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import frontmatter

from agents.utils import VAULT_ENTRY_DIRS

if TYPE_CHECKING:
    from pathlib import Path

GRANULARITIES = ("entry", "section")

_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
MIN_SECTION = 200  # chars: smaller sections are folded into their neighbour
MAX_SECTION = 1500  # chars: larger sections are cut on paragraph boundaries
ENTRY_CHARS = 6000  # chars of body kept for an entry-level chunk


@dataclass
class FileStamp:
    mtime_ns: int
    size: int


@dataclass
class Entry:
    entry_id: str
    path: str  # relative to the vault
    title: str
    type: str
    domain: list[str]
    tags: list[str]
    body: str
    sha: str


@dataclass
class Chunk:
    chunk_id: str
    entry_id: str
    path: str
    title: str
    heading: str
    body: str  # section text as written, for display and reranking
    text: str  # context prefix + body: what is embedded and tokenised
    meta: dict[str, Any] = field(default_factory=dict)


def scan(vault: Path) -> dict[str, FileStamp]:
    """Every entry file in the vault, keyed by vault-relative path."""
    found: dict[str, FileStamp] = {}
    for name in VAULT_ENTRY_DIRS:
        folder = vault / name
        if not folder.is_dir():
            continue
        for file in folder.rglob("*.md"):
            st = file.stat()
            found[file.relative_to(vault).as_posix()] = FileStamp(st.st_mtime_ns, st.st_size)
    return found


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def load(vault: Path, rel: str) -> Entry | None:
    """Parse one entry file; None when it has no `id` (templates, drafts without frontmatter)."""
    raw = (vault / rel).read_bytes()
    try:
        post = frontmatter.loads(raw.decode("utf-8"))
    except Exception:
        return None
    meta = post.metadata
    entry_id = str(meta.get("id") or "").strip()
    if not entry_id:
        return None
    return Entry(
        entry_id=entry_id,
        path=rel,
        title=str(meta.get("title") or entry_id),
        type=str(meta.get("type") or ""),
        domain=_as_list(meta.get("domain")),
        tags=_as_list(meta.get("tags")),
        body=post.content,
        sha=hashlib.sha256(raw).hexdigest(),
    )


def _sections(body: str) -> list[tuple[list[str], str]]:
    """(heading path, text) for each heading-delimited section, fences respected."""
    out: list[tuple[list[str], list[str]]] = [([], [])]
    stack: list[tuple[int, str]] = []
    in_fence = False
    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m:
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2).strip()))
            out.append(([h for _, h in stack], []))
        else:
            out[-1][1].append(line)
    return [(path, "\n".join(lines).strip()) for path, lines in out]


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_SECTION:
        return [text]
    parts: list[str] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        if buf and len(buf) + len(para) > MAX_SECTION:
            parts.append(buf)
            buf = ""
        buf = f"{buf}\n\n{para}" if buf else para
        while len(buf) > MAX_SECTION * 2:  # one huge paragraph or code block
            parts.append(buf[:MAX_SECTION])
            buf = buf[MAX_SECTION:]
    if buf:
        parts.append(buf)
    return parts


def _prefix(entry: Entry, heading: str) -> str:
    head = entry.title
    if entry.tags:
        head += "\n标签：" + "，".join(entry.tags)
    if heading:
        head += "\n小节：" + heading
    return head


def chunks(entry: Entry, granularity: str = "section") -> list[Chunk]:
    meta = {"type": entry.type, "domain": entry.domain, "tags": entry.tags}

    def make(i: int, heading: str, body: str) -> Chunk:
        return Chunk(
            chunk_id=f"{entry.entry_id}#{i}",
            entry_id=entry.entry_id,
            path=entry.path,
            title=entry.title,
            heading=heading,
            body=body,
            text=f"{_prefix(entry, heading)}\n\n{body}".strip(),
            meta=meta,
        )

    if granularity == "entry":
        return [make(0, "", entry.body.strip()[:ENTRY_CHARS])]

    merged: list[tuple[str, str]] = []
    for path, text in _sections(entry.body):
        heading = " / ".join(path)
        if not text and merged:
            continue
        if merged and len(merged[-1][1]) < MIN_SECTION:
            prev_heading, prev_text = merged[-1]
            merged[-1] = (prev_heading or heading, f"{prev_text}\n\n{text}".strip())
        else:
            merged.append((heading, text))
    if len(merged) > 1 and len(merged[-1][1]) < MIN_SECTION:
        heading, text = merged.pop()
        merged[-1] = (merged[-1][0], f"{merged[-1][1]}\n\n{text}".strip())

    out: list[Chunk] = []
    for heading, text in merged or [("", "")]:
        for part in _split_long(text):
            out.append(make(len(out), heading, part))
    return out
