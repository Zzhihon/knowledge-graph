"""Incrementally read allowed Claude Code / Codex sessions into budgeted batches.

State lives under `<vault>/.kg/capture/` (gitignored):

- `config.yaml`  which session folders to read, which project folders are allowed, budgets
- `state.json`   per session file: byte offset already read, plus what the first lines said
- `inbox/<batch>/` one markdown digest per session part, and `manifest.json`

A batch is complete once its digests and manifest are on disk; only then is the
cursor in `state.json` moved, so an interrupted scan simply re-reads the same
bytes next time. Whoever processes a batch writes `result.md` into its folder;
batches without one count as pending.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from kg.capture.sessions import HEARTBEAT, Block, SessionInfo, iter_blocks

TZ = ZoneInfo("Asia/Shanghai")
HOME = Path.home()
RESULT_FILE = "result.md"

_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"}
_SHELL_TOOLS = {"Bash", "exec", "exec_command", "shell", "local_shell"}
_LOOKUP_TOOLS = {"WebSearch", "WebFetch", "web_search", "Grep"}
_PROGRESS_LIMIT = 300  # assistant notes before the reply that closes an exchange
_ERRORS_PER_TURN = 3


@dataclass(frozen=True)
class AllowRoot:
    path: Path
    container: bool  # each direct child is its own project (e.g. ~/Github-Projects)
    exact: bool  # only this folder itself, not its children (e.g. the home folder)


@dataclass(frozen=True)
class Budget:
    max_chars_per_batch: int = 120_000
    max_chars_per_session: int = 30_000
    max_sessions_per_batch: int = 25
    min_user_chars: int = 60
    idle_minutes: int = 30
    max_pending_batches: int = 2


@dataclass(frozen=True)
class CaptureConfig:
    root: Path
    sources: dict[str, Path]
    allow: tuple[AllowRoot, ...]
    deny: tuple[Path, ...]
    start_date: date
    budget: Budget
    brief: dict[str, Any] = field(default_factory=dict)

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"


def _expand(raw: str | None) -> Path:
    if not raw:
        # A bare `~` in YAML is null, not the home folder.
        raise ValueError('配置里有空路径；主目录要写成带引号的 "~"')
    return Path(os.path.expanduser(raw)).resolve()


def load_capture_config(vault: Path) -> CaptureConfig:
    root = vault / ".kg" / "capture"
    file = root / "config.yaml"
    if not file.exists():
        raise FileNotFoundError(f"缺少 {file}，照 capture.yaml.example 写一份")
    raw: dict[str, Any] = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    allow: list[AllowRoot] = []
    for item in raw.get("allow", []):
        if isinstance(item, str):
            allow.append(AllowRoot(_expand(item), container=False, exact=False))
        else:
            allow.append(AllowRoot(_expand(item["path"]), bool(item.get("container")),
                                   bool(item.get("exact"))))
    sources = {name: _expand(path) for name, path in (raw.get("sources") or {}).items()}
    start = raw.get("start_date") or (datetime.now(TZ).date() - timedelta(days=14))
    budget = Budget(**(raw.get("budget") or {}))
    return CaptureConfig(
        root=root,
        sources=sources or {"claude": _expand("~/.claude/projects"),
                            "codex": _expand("~/.codex/sessions")},
        allow=tuple(allow),
        deny=tuple(_expand(p) for p in raw.get("deny", [])),
        start_date=start if isinstance(start, date) else date.fromisoformat(str(start)),
        budget=budget,
        brief=raw.get("brief") or {},
    )


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def project_for(cwd: str, config: CaptureConfig) -> str | None:
    """The project name for a session's folder, or None when it may not be read."""
    if not cwd:
        return None
    path = Path(cwd).resolve()
    if any(_within(path, denied) for denied in config.deny):
        return None
    best: AllowRoot | None = None
    for root in config.allow:
        matched = path == root.path if root.exact else _within(path, root.path)
        if matched and (best is None or len(root.path.parts) > len(best.path.parts)):
            best = root
    if best is None:
        return None
    if best.container and path != best.path:
        return path.relative_to(best.path).parts[0]
    return best.path.name or "home"


@dataclass
class FileState:
    offset: int = 0
    session_id: str = ""
    cwd: str = ""
    branch: str = ""
    skip: str = ""

    def info(self, source: str) -> SessionInfo:
        return SessionInfo(source, self.session_id, self.cwd, self.branch, self.skip)


@dataclass
class Part:
    source: str
    file: Path
    info: SessionInfo
    project: str
    start: int
    end: int
    text: str
    first: str
    last: str
    user_chars: int


@dataclass
class ScanResult:
    batch_id: str | None = None
    parts: list[Part] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    remaining: int = 0  # session files that still have unread content
    pending: list[str] = field(default_factory=list)


def _stamp(raw: str) -> str:
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return moment.astimezone(TZ).strftime("%m-%d %H:%M")


def _operations(tools: list[str]) -> str:
    """What an exchange did, in one line: files changed, commands run, what was looked up."""
    changed: list[str] = []
    commands: list[str] = []
    lookups: list[str] = []
    others: dict[str, int] = {}
    for tool in tools:
        name, _, arg = tool.partition(": ")
        if name in _EDIT_TOOLS:
            changed.extend(a.strip() for a in arg.split(",") if a.strip())
        elif name in _SHELL_TOOLS:
            commands.append(arg)
        elif name in _LOOKUP_TOOLS and arg:
            lookups.append(arg)
        else:
            others[name] = others.get(name, 0) + 1
    parts: list[str] = []
    if changed:
        files = list(dict.fromkeys(Path(f).name for f in changed))
        more = f" 等 {len(files)} 个文件" if len(files) > 8 else ""
        parts.append(f"改了 {'、'.join(files[:8])}{more}")
    if commands:
        examples = "；".join(_clip(c, 80) for c in list(dict.fromkeys(commands))[:2])
        parts.append(f"跑了 {len(commands)} 条命令（如 {examples}）")
    if lookups:
        parts.append(f"查了 {'；'.join(_clip(q, 80) for q in list(dict.fromkeys(lookups))[:3])}")
    if others:
        common = sorted(others.items(), key=lambda x: -x[1])[:5]
        parts.append("、".join(f"{n}×{c}" for n, c in common))
    return f"[操作] {'；'.join(parts)}" if parts else ""


def _render_turn(blocks: list[Block]) -> str:
    """One exchange as markdown: the user's words, the assistant's last reply in full,
    earlier progress notes shortened, and the tool calls folded into one line."""
    replies = [i for i, b in enumerate(blocks) if b.role == "assistant"]
    # A scheduled run's report is not a conversation: one short line is enough.
    scheduled = any(b.role == "user" and b.text == HEARTBEAT for b in blocks)
    final = replies[-1] if replies and not scheduled else -1
    lines: list[str] = []
    errors: list[str] = []
    for index, block in enumerate(blocks):
        if block.role == "user":
            lines.append(f"[用户 {_stamp(block.timestamp)}] {block.text}")
        elif block.role == "assistant":
            text = block.text if index == final else _clip(block.text, _PROGRESS_LIMIT)
            lines.append(f"[助手] {text}")
        elif block.role == "error" and len(errors) < _ERRORS_PER_TURN:
            errors.append(f"[报错] {block.text}")
    operations = _operations([b.text for b in blocks if b.role == "tool"])
    return "\n".join(lines + errors + ([operations] if operations else []))


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def _read_part(
    source: str, file: Path, state: FileState, config: CaptureConfig, room: int
) -> tuple[Part | None, FileState]:
    """Read whole exchanges of one session until `room` characters; returns the moved cursor."""
    size = file.stat().st_size
    start = state.offset if state.offset <= size else 0
    with file.open("rb") as handle:
        handle.seek(start)
        data = handle.read()
    info = state.info(source) if start else SessionInfo(source)
    turns: list[str] = []
    stamps: list[str] = []
    length = user_chars = 0
    end = start
    current: list[Block] = []
    consumed = start  # end of the last line read, whatever it held
    full = False

    def close() -> bool:
        """Add the open exchange if it fits; False once the room is used up."""
        nonlocal length, user_chars, end
        if not current:
            end = consumed
            return True
        text = _render_turn(current)
        if turns and length + len(text) > room:
            return False
        turns.append(text if turns or len(text) <= room else _clip(text, room))
        length += len(turns[-1]) + 2
        user_chars += sum(len(b.text) for b in current if b.role == "user" and b.text != HEARTBEAT)
        stamps.extend(b.timestamp for b in current if b.timestamp)
        current.clear()
        end = consumed
        return True

    for block in iter_blocks(source, data, start, info):
        if info.skip:
            break
        if block.role == "user" and current and not close():
            full = True
            break
        if block.role:
            current.append(block)
        consumed = block.end_offset
    if not info.skip and not full:
        close()
    if info.skip:
        # Not ours to read: jump to the last complete line so it is never looked at again.
        end = start + data.rfind(b"\n") + 1 if b"\n" in data else start
    moved = FileState(end, info.session_id, info.cwd, info.branch, info.skip)
    if info.skip or not turns:
        return None, moved
    project = project_for(info.cwd, config) or ""
    text = "\n\n".join(turns)
    return Part(source, file, info, project, start, end, text, stamps[0] if stamps else "",
                stamps[-1] if stamps else "", user_chars), moved


def _load_state(config: CaptureConfig) -> dict[str, FileState]:
    file = config.root / "state.json"
    if not file.exists():
        return {}
    raw = json.loads(file.read_text(encoding="utf-8"))
    return {path: FileState(**value) for path, value in raw.get("files", {}).items()}


def _save_state(config: CaptureConfig, states: dict[str, FileState]) -> None:
    file = config.root / "state.json"
    tmp = file.with_suffix(".json.tmp")
    data = {"files": {path: vars(value) for path, value in sorted(states.items())}}
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(file)


def pending_batches(config: CaptureConfig) -> list[str]:
    if not config.inbox.is_dir():
        return []
    return sorted(
        d.name for d in config.inbox.iterdir()
        if d.is_dir() and (d / "manifest.json").exists() and not (d / RESULT_FILE).exists()
    )


def _candidates(config: CaptureConfig, states: dict[str, FileState], now: float) -> list[Path]:
    earliest = datetime.combine(config.start_date, datetime.min.time(), TZ).timestamp()
    idle_before = now - config.budget.idle_minutes * 60
    found: list[tuple[float, Path]] = []
    for source, folder in config.sources.items():
        if not folder.is_dir():
            continue
        for file in folder.rglob("*.jsonl"):
            if source == "claude" and "subagents" in file.parts:
                continue
            stat = file.stat()
            if not earliest <= stat.st_mtime <= idle_before:
                continue
            state = states.get(str(file))
            if state and (state.skip or state.offset >= stat.st_size):
                continue
            found.append((stat.st_mtime, file))
    return [file for _, file in sorted(found)]


def _source_of(file: Path, config: CaptureConfig) -> str:
    return next(name for name, folder in config.sources.items() if folder in file.parents)


def scan(
    config: CaptureConfig, *, now: datetime | None = None, dry_run: bool = False
) -> ScanResult:
    """Read the next budgeted batch of allowed sessions into the inbox."""
    moment = now or datetime.now(TZ)
    result = ScanResult(pending=pending_batches(config))
    if len(result.pending) >= config.budget.max_pending_batches:
        return result
    states = _load_state(config)
    budget = config.budget
    room = budget.max_chars_per_batch
    moved: dict[str, FileState] = {}
    candidates = _candidates(config, states, moment.timestamp())
    for index, file in enumerate(candidates):
        if room <= 0 or len(result.parts) >= budget.max_sessions_per_batch:
            result.remaining = len(candidates) - index
            break
        key = str(file)
        state = states.get(key, FileState())
        if state.cwd and not project_for(state.cwd, config):
            moved[key] = _mark_skipped(file, state, "not-allowed", result)
            continue
        part, cursor = _read_part(_source_of(file, config), file, state, config,
                                  min(room, budget.max_chars_per_session))
        if cursor.skip:
            moved[key] = _mark_skipped(file, cursor, cursor.skip, result)
            continue
        # Which folder a session ran in is only known once a turn has been read.
        if cursor.cwd and not project_for(cursor.cwd, config):
            moved[key] = _mark_skipped(file, cursor, "not-allowed", result)
            continue
        if part is None:
            moved[key] = cursor
            continue
        whole = state.offset == 0 and cursor.offset >= file.stat().st_size
        if whole and part.user_chars < budget.min_user_chars:
            moved[key] = _mark_skipped(file, cursor, "too-short", result)
            continue
        result.parts.append(part)
        moved[key] = cursor
        room -= len(part.text)
    if dry_run:
        return result
    if result.parts:
        result.batch_id = moment.strftime("%Y%m%d-%H%M%S")
        _write_batch(config, result.batch_id, result.parts, moment)
    states.update(moved)
    if moved:
        config.root.mkdir(parents=True, exist_ok=True)
        _save_state(config, states)
    return result


def _mark_skipped(file: Path, cursor: FileState, reason: str, result: ScanResult) -> FileState:
    result.skipped[reason] = result.skipped.get(reason, 0) + 1
    size = file.stat().st_size
    return FileState(max(cursor.offset, size), cursor.session_id, cursor.cwd, cursor.branch,
                     reason)


def _write_batch(config: CaptureConfig, batch_id: str, parts: list[Part], now: datetime) -> None:
    folder = config.inbox / batch_id
    tmp = config.inbox / f".{batch_id}.tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    entries = []
    for number, part in enumerate(parts, start=1):
        name = f"{number:02d}-{part.source}-{part.info.session_id[:8] or 'unknown'}.md"
        header = {
            "source": part.source,
            "session": part.info.session_id,
            "project": part.project,
            "cwd": part.info.cwd,
            "branch": part.info.branch,
            "from": _stamp(part.first),
            "to": _stamp(part.last),
            "file": str(part.file),
            "bytes": f"{part.start}-{part.end}",
        }
        body = "---\n" + yaml.safe_dump(header, allow_unicode=True, sort_keys=False) + "---\n\n"
        (tmp / name).write_text(body + part.text + "\n", encoding="utf-8")
        entries.append({**header, "digest": name, "chars": len(part.text)})
    manifest = {"batch": batch_id, "created": now.isoformat(timespec="seconds"), "parts": entries}
    (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    tmp.rename(folder)
