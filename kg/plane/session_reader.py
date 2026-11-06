"""Read Claude Code / Codex session files into small digests, incrementally.

The in-progress board needs, per session: where it ran, when it started and last moved, how
many turns it has, what the user said last, what the agent concluded last and whether that was
a question. Session files are append-only JSONL and some run to hundreds of megabytes, so every
digest remembers the byte offset it has read up to plus the file's size and mtime:

- size and mtime unchanged  -> the stored digest is reused, the file is not opened;
- the file grew             -> only the appended complete lines are parsed;
- the file shrank           -> it was rewritten, read again from the start.

Parsing goes through `kg.capture.sessions` (same turn rules and the same skip rules as the
nightly capture: sub-agents, headless runs, Codex copies of imported sessions, runs that
agent-autoloop dispatched). Two extra skips apply here: `codex exec` runs (headless Codex) and
sessions whose folder is not on the capture allow list. Texts are redacted before they are
stored, so the cursor file never holds what the board must not show.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import TYPE_CHECKING, Any

from kg.capture.sessions import HEARTBEAT, SessionInfo, iter_blocks
from kg.plane.progress import excerpt, question_reason, redact

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# Bump when what a digest holds or how it is judged changes: older caches are then rebuilt.
CURSOR_VERSION = 2
CHUNK = 8 << 20  # bytes read at a time; a long session is never loaded whole
USER_CHARS = 300
AGENT_CHARS = 600
TAIL_CHARS = 400
# Tools whose pending call means the agent stopped to ask the user.
ASK_TOOLS = ("AskUserQuestion", "ExitPlanMode", "request_user_input")
_TRUNCATED = "…[截断]"
# Codex copies of Claude sessions that the capture's own marker misses: the copied user turns
# keep Claude's command tags or a placeholder for blocks Codex could not import.
_CODEX_IMPORT_MARKS = ("[external unsupported block", "<command-name>", "<local-command-",
                       "<command-message>")


@dataclass
class Digest:
    """What the board shows of one session file, and its read cursor (`offset`, `size`, `mtime`)."""

    source: str
    file: str
    session_id: str = ""
    cwd: str = ""
    branch: str = ""
    skip: str = ""
    offset: int = 0
    size: int = 0
    mtime: float = 0.0
    started: int | None = None
    last_ts: int | None = None
    turns: int = 0
    scheduled: bool = False  # a Codex automation thread woken by heartbeats
    last_user: str = ""
    last_user_ts: int | None = None
    last_agent: str = ""  # head of the last agent reply (conclusion first)
    last_agent_tail: str = ""  # its end, where a question to the user would be
    last_agent_ts: int | None = None
    last_question: bool = False
    last_reason: str = ""  # why it counts as a question (kg.plane.progress.question_reason)
    last_event: str = ""  # user | assistant | tool | ask | error
    last_tool: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Digest:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


def _epoch(stamp: str) -> int | None:
    if not stamp:
        return None
    try:
        return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _raw_text(source: str, line: bytes) -> str:
    """The full text of an assistant record (capture clips long replies at the head)."""
    try:
        record = json.loads(line)
    except ValueError:
        return ""
    if source == "claude":
        content = (record.get("message") or {}).get("content")
    else:
        content = (record.get("payload") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content
                         if isinstance(p, dict) and p.get("type") in ("text", "output_text"))
    return ""


def _codex_headless(first_line: bytes) -> bool:
    try:
        payload = json.loads(first_line).get("payload") or {}
    except (ValueError, AttributeError):
        return False
    return payload.get("originator") == "codex_exec" or payload.get("source") == "exec"


class _Reader:
    """Folds blocks into a digest; keeps the raw line of the block being looked at."""

    def __init__(self, digest: Digest) -> None:
        self.d = digest
        self.skip = ""

    def block(self, role: str, text: str, stamp: str, raw_line: Callable[[], bytes]) -> None:
        d = self.d
        ts = _epoch(stamp)
        if ts is not None:
            d.started = d.started if d.started is not None else ts
            d.last_ts = max(d.last_ts or ts, ts)
        if role == "user" and d.source == "codex" and any(m in text for m in _CODEX_IMPORT_MARKS):
            self.skip = "imported"
            return
        if role == "user":
            if text == HEARTBEAT:
                d.scheduled = True
            else:
                d.turns += 1
            d.last_user = excerpt(redact(text), USER_CHARS)
            d.last_user_ts = ts
            d.last_event, d.last_tool = "user", ""
        elif role == "assistant":
            full = _raw_text(d.source, raw_line()) if text.endswith(_TRUNCATED) else text
            full = full.strip() or text
            d.last_agent = excerpt(redact(full), AGENT_CHARS)
            d.last_agent_tail = excerpt(redact(full), TAIL_CHARS, tail=True)
            d.last_agent_ts = ts
            d.last_reason = question_reason(full)
            d.last_question = bool(d.last_reason)
            d.last_event, d.last_tool = "assistant", ""
        elif role == "tool":
            name = text.partition(":")[0].strip()
            d.last_event = "ask" if name.startswith(ASK_TOOLS) else "tool"
            d.last_tool = name
        elif role == "error":
            d.last_event = "error"


if TYPE_CHECKING:
    # observe(role, text, timestamp, raw_line, digest-so-far)
    Observer = Callable[[str, str, str, Callable[[], bytes], "Digest"], None]


def read_session(source: str, file: Path, previous: Digest | None, *,
                 allowed: Callable[[str], bool] | None = None,
                 chunk: int = CHUNK,
                 observe: Observer | None = None) -> Digest:
    """Bring `previous` up to date with `file`; reads only what was appended since.

    `observe(role, text, timestamp, raw_line, digest)` is called before each block is folded in,
    so a caller can see the digest as it stood at every point of the session (the behaviour
    check in `kg.plane.waiting_eval` replays the board's judgement that way).
    """
    stat = file.stat()
    size, mtime = stat.st_size, stat.st_mtime
    if previous and previous.size == size and previous.mtime == mtime:
        return previous
    if previous and previous.skip and previous.skip != "not-allowed" and size >= previous.size:
        previous.size, previous.mtime, previous.offset = size, mtime, size
        return previous
    fresh = (previous is None or previous.offset > size or previous.skip == "not-allowed")
    if fresh:
        digest = Digest(source, str(file))
    else:
        assert previous is not None
        digest = previous
    info = SessionInfo(source, digest.session_id, digest.cwd, digest.branch, "")
    reader = _Reader(digest)
    start = 0 if fresh else digest.offset
    with file.open("rb") as handle:
        handle.seek(start)
        pending = b""
        base = start  # file offset where `pending` begins
        while True:
            data = handle.read(chunk)
            if not data:
                break
            buf = pending + data
            if base == 0 and source == "codex" and _codex_headless(buf.split(b"\n", 1)[0]):
                info.skip = "headless"
            line_start = line_end = base
            for block in iter_blocks(source, buf, base, info):
                if block.end_offset != line_end:
                    line_start, line_end = line_end, block.end_offset
                if info.skip:
                    break
                if allowed is not None and info.cwd and not allowed(info.cwd):
                    info.skip = "not-allowed"
                    break
                if block.role:
                    lo, hi = line_start - base, line_end - base

                    def raw(lo: int = lo, hi: int = hi, buf: bytes = buf) -> bytes:
                        return buf[lo:hi]

                    if observe is not None:
                        observe(block.role, block.text, block.timestamp, raw, digest)
                    reader.block(block.role, block.text, block.timestamp, raw)
                    if reader.skip:
                        info.skip = reader.skip
                        break
                digest.offset = block.end_offset
            if info.skip:
                break
            consumed = digest.offset - base
            pending, base = buf[consumed:], digest.offset
    digest.session_id = info.session_id or digest.session_id or file.stem
    digest.cwd, digest.branch = info.cwd or digest.cwd, info.branch or digest.branch
    digest.size, digest.mtime = size, mtime
    if info.skip:
        digest.skip, digest.offset = info.skip, size
    return digest


def load_cursor(path: Path) -> dict[str, Digest]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}  # a damaged cache is rebuilt, not fatal
    if raw.get("version") != CURSOR_VERSION:
        return {}
    return {key: Digest.from_json(value) for key, value in (raw.get("files") or {}).items()}


def save_cursor(path: Path, digests: dict[str, Digest]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    files = {key: d.to_json() for key, d in sorted(digests.items())}
    data = {"version": CURSOR_VERSION, "files": files}
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
