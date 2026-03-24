"""Shared utilities for the knowledge graph CLI.

Provides common helpers for ID generation, entry loading,
directory mapping, and text normalization.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter

# Mapping from entry type key to vault directory name
_TYPE_DIR_MAP: dict[str, str] = {
    "principle": "01-Principles",
    "pattern": "02-Patterns",
    "debug": "03-Debug",
    "architecture": "04-Architecture",
    "research": "05-Research",
    "team": "06-Team",
    "problem": "08-Problems",
    "interview": "09-Interview",
}


def compute_content_hash(file_path: Path) -> str:
    """SHA-256 hex digest of raw file bytes.

    Args:
        file_path: 文件路径.

    Returns:
        64 字符的十六进制 SHA-256 摘要.
    """
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def slugify(text: str, max_length: int = 60) -> str:
    """Convert arbitrary text into a URL-safe slug.

    Handles CJK characters by keeping them as-is (they are valid in
    filenames), and converts Latin characters to lowercase ASCII.

    Args:
        text: The source text to slugify.
        max_length: Maximum slug length before truncation.

    Returns:
        A lowercased, hyphen-separated slug string.
    """
    # Normalize unicode to NFC form
    text = unicodedata.normalize("NFC", text)
    # Replace common separators with hyphens
    text = re.sub(r"[\s_/\\]+", "-", text)
    # Remove characters that are not alphanumeric, CJK, or hyphens
    text = re.sub(r"[^\w\u4e00-\u9fff\u3400-\u4dbf-]", "", text)
    # Collapse multiple hyphens
    text = re.sub(r"-{2,}", "-", text)
    # Strip leading/trailing hyphens and lowercase
    text = text.strip("-").lower()
    # Truncate to max_length at a hyphen boundary if possible
    if len(text) > max_length:
        truncated = text[:max_length]
        last_hyphen = truncated.rfind("-")
        if last_hyphen > max_length // 2:
            truncated = truncated[:last_hyphen]
        text = truncated.rstrip("-")
    return text


def generate_id(title: str) -> str:
    """Generate a knowledge entry ID in the format ke-{YYYYMMDD}-{slug}.

    Args:
        title: The entry title used to derive the slug portion.

    Returns:
        A unique-ish ID string like 'ke-20250226-goroutine-scheduling'.
    """
    date_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    slug = slugify(title, max_length=50)
    return f"ke-{date_str}-{slug}"


def get_entry_dir(entry_type: str) -> str:
    """Map an entry type key to its vault directory name.

    Args:
        entry_type: One of 'principle', 'pattern', 'debug',
                    'architecture', 'research', 'team', 'problem'.

    Returns:
        The directory name such as '01-Principles'.

    Raises:
        ValueError: If the entry_type is not recognized.
    """
    normalized = entry_type.strip().lower()
    if normalized not in _TYPE_DIR_MAP:
        valid = ", ".join(sorted(_TYPE_DIR_MAP.keys()))
        raise ValueError(
            f"未知的条目类型: '{entry_type}'. 有效类型: {valid}"
        )
    return _TYPE_DIR_MAP[normalized]


def load_entries(
    base_path: Path,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Load all knowledge entries from the vault directories.

    Scans 01-06 directories under base_path for .md files, parses
    YAML frontmatter, and returns structured dicts.

    Args:
        base_path: Root path of the knowledge vault.
        filters: Optional dict of frontmatter field filters.
                 Supported keys: domain, type, depth, status.

    Returns:
        List of dicts, each containing 'metadata' (frontmatter dict),
        'content' (body text), and 'path' (file Path).
    """
    entries: list[dict[str, Any]] = []
    target_dirs = [
        "01-Principles",
        "02-Patterns",
        "03-Debug",
        "04-Architecture",
        "05-Research",
        "06-Team",
        "08-Problems",
        "09-Interview",
    ]

    for dir_name in target_dirs:
        dir_path = base_path / dir_name
        if not dir_path.is_dir():
            continue
        for md_file in sorted(dir_path.rglob("*.md")):
            try:
                post = frontmatter.load(str(md_file))
            except Exception:
                # Skip files that cannot be parsed
                continue

            entry: dict[str, Any] = {
                "metadata": dict(post.metadata),
                "content": post.content,
                "path": md_file,
            }

            if filters and not _matches_filters(entry["metadata"], filters):
                continue

            entries.append(entry)

    return entries


def _matches_filters(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Check whether entry metadata matches all provided filters.

    Comparison is case-insensitive for string values. List-typed
    metadata fields match if the filter value is contained in the list.
    """
    for key, expected in filters.items():
        if expected is None:
            continue
        actual = metadata.get(key)
        if actual is None:
            return False
        if isinstance(actual, list):
            # For list fields (e.g. tags), check membership
            if str(expected).lower() not in [str(v).lower() for v in actual]:
                return False
        elif isinstance(actual, str) and isinstance(expected, str):
            if actual.lower() != expected.lower():
                return False
        elif actual != expected:
            return False
    return True
