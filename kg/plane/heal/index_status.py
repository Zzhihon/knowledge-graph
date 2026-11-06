"""`<vault>/.kg/index/status.json`: whether the search index is known to be stale.

The index builder (or the heal action for an `index.*` incident) marks it stale with a reason;
a successful rebuild marks it fresh. Search reads `search_warning()` and, when it is not None,
tells the caller "结果可能不全" next to the results. Missing or unreadable file means fresh:
not knowing is not a reason to scare every search.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def status_path(vault: Path) -> Path:
    return vault / ".kg" / "index" / "status.json"


@dataclass(frozen=True)
class IndexStatus:
    stale: bool = False
    since: int | None = None  # when it went stale
    reason: str = ""
    incident: int | None = None
    updated_at: int | None = None


def read_index_status(vault: Path) -> IndexStatus:
    file = status_path(vault)
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return IndexStatus()
    if not isinstance(raw, dict):
        return IndexStatus()
    return IndexStatus(bool(raw.get("stale")), raw.get("since"), str(raw.get("reason") or ""),
                       raw.get("incident"), raw.get("updated_at"))


def _write(vault: Path, status: IndexStatus) -> IndexStatus:
    file = status_path(vault)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(status), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, file)
    return status


def mark_index_stale(vault: Path, reason: str, *, incident: int | None = None,
                     now: int | None = None) -> IndexStatus:
    now = int(time.time()) if now is None else now
    current = read_index_status(vault)
    since = current.since if current.stale and current.since else now
    return _write(vault, IndexStatus(True, since, reason, incident, now))


def mark_index_fresh(vault: Path, *, now: int | None = None) -> IndexStatus:
    now = int(time.time()) if now is None else now
    return _write(vault, IndexStatus(False, None, "", None, now))


def search_warning(vault: Path) -> str | None:
    """What a search response should say about the index, or None when it is fine."""
    status = read_index_status(vault)
    if not status.stale:
        return None
    return f"索引可能过期，结果可能不全（{status.reason or '上次建索引失败'}）"
