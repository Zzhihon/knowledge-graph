"""Stable resource ids: `<kind>:<namespace>:<local id>`, e.g. `service:launchd:com.example.web`.

Alerts, incidents and knowledge entries refer to a resource by this id, so once a collector
has written an id it must never change. Rules:

- `kind` is one of KINDS, lowercase.
- Then one or more parts separated by `:`. The first part names where the id comes from
  (`launchd`, `port`, `orca`, `claude`, ...); the last part is the id in that source, taken
  verbatim (a launchd label, a port number, an artifact id, a session uuid). A source that is
  unique on its own may use a single part (`project:<slug>`, `egress:<line name>`).
- A part is non-empty and has no `:`, whitespace or control characters. Anything else is kept
  as is (case, dots, non-ASCII), because rewriting it would make the id depend on the rewrite.
- At most 256 characters in total.

`make_id` refuses parts that break these rules instead of cleaning them up: a silently
"fixed" id would collide or drift the first time the cleanup changes.
"""

from __future__ import annotations

import re

KINDS = ("service", "egress", "artifact", "project", "session", "rule", "index")
MAX_LENGTH = 256

_PART = re.compile(r"[^\s:\x00-\x1f\x7f]+")


def check_part(part: str) -> str:
    if not isinstance(part, str) or not _PART.fullmatch(part):
        raise ValueError(f"资源 id 的分段不能为空，也不能含冒号、空白或控制字符：{part!r}")
    return part


def make_id(kind: str, *parts: str) -> str:
    """Join kind and parts into a resource id, refusing anything that is not stable."""
    if kind not in KINDS:
        raise ValueError(f"未知的资源类型 {kind!r}，可选：{', '.join(KINDS)}")
    if not parts:
        raise ValueError("资源 id 至少要有一段本地 id")
    rid = ":".join((kind, *(check_part(p) for p in parts)))
    if len(rid) > MAX_LENGTH:
        raise ValueError(f"资源 id 超过 {MAX_LENGTH} 字符：{rid[:60]}…")
    return rid


def parse_id(rid: str) -> tuple[str, tuple[str, ...]]:
    """Split an id into (kind, parts); raises ValueError when it does not follow the rules."""
    kind, _, rest = rid.partition(":")
    parts = tuple(rest.split(":")) if rest else ()
    if make_id(kind, *parts) != rid:  # pragma: no cover - make_id raises first
        raise ValueError(f"资源 id 不合规：{rid!r}")
    return kind, parts


def is_valid_id(rid: str) -> bool:
    try:
        parse_id(rid)
    except ValueError:
        return False
    return True
