"""Turn Claude Code and Codex session logs into compact, readable turns.

Session files are append-only JSONL. Most of their bytes are tool output and
duplicated request blobs; what carries knowledge is what the user said, what
the assistant concluded, which files and commands were touched, and which
errors came up. Each parsed line yields zero or more `Block`s tagged with the
byte offset where that line ends, so a caller can stop at any block and resume
from the exact place later.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

# The dispatch prompt agent-autoloop gives every run; such sessions are our own output.
AUTOLOOP_PROMPT_PREFIX = "Agent 自动化中台按规则"

_USER_LIMIT = 4000
_ASSISTANT_LIMIT = 3000
_TOOL_ARG_LIMIT = 160
_ERROR_LIMIT = 240

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_LOCAL_TAGS = re.compile(
    r"<(local-command-stdout|local-command-stderr|command-message|command-args|command-name)>"
    r".*?</\1>",
    re.S,
)
_CODEX_PREAMBLES = ("# AGENTS.md instructions", "<environment_context>", "<user_instructions>",
                    "<INSTRUCTIONS>", "<turn_aborted>", "<recommended_plugins>",
                    "<in-app-browser-context")
# A Codex automation wakes its thread with a heartbeat; what follows is a scheduled run.
HEARTBEAT = "（定时触发）"
_PASTE = re.compile(r"<pasted_content[^>]*>(.*?)(?:</pasted_content>|\Z)", re.S)
_PASTE_HEAD = 160
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)
_NOISE_USER = ("[Request interrupted by user", "This session is being continued from a previous",
               "<task-notification", "<artifact-content-")
_NOISE_ASSISTANT = ("API Error:",)
# Codex keeps copies of Claude Code sessions it imported; the originals are read from Claude.
_IMPORTED = "[external_agent_tool_call"


@dataclass(frozen=True)
class Block:
    """One rendered piece of a session: `role` is user, assistant, tool or error."""

    role: str
    text: str
    timestamp: str
    end_offset: int


@dataclass
class SessionInfo:
    """What the first lines tell about a session; `skip` names why it is not worth reading."""

    source: str
    session_id: str = ""
    cwd: str = ""
    branch: str = ""
    skip: str = ""


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " …[截断]"


def _fold_pastes(text: str) -> str:
    """Large pasted material (configs, logs) becomes its size and first line; typed words stay."""

    def fold(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        head = body[:_PASTE_HEAD].replace("\n", " ")
        if len(body) <= _PASTE_HEAD:
            return f"〔粘贴：{body}〕"
        return f"〔粘贴 {len(body)} 字：{head}…〕"

    return _PASTE.sub(fold, text)


def _clean_user_text(text: str) -> str:
    text = _fold_pastes(text)
    name = _COMMAND_NAME.search(text)
    if name:
        args = _COMMAND_ARGS.search(text)
        text = f"{name.group(1).strip()} {args.group(1).strip() if args else ''}"
    text = _LOCAL_TAGS.sub("", _REMINDER.sub("", text))
    return text.strip()


def _tool_summary(name: str, args: Any) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            files = _PATCH_FILE.findall(args)
            shown = ", ".join(files) if files else _clip(args, _TOOL_ARG_LIMIT)
            return f"{name}: {shown}"
    if not isinstance(args, dict):
        return name
    for key in ("file_path", "path", "command", "cmd", "pattern", "url", "query", "description"):
        value = args.get(key)
        if isinstance(value, list):
            value = " ".join(str(v) for v in value)
        if isinstance(value, str) and value.strip():
            return f"{name}: {_clip(value.replace(chr(10), ' '), _TOOL_ARG_LIMIT)}"
    return name


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    return ""


def claude_blocks(record: dict[str, Any], end_offset: int, info: SessionInfo) -> list[Block]:
    """Blocks from one Claude Code JSONL record (only user/assistant records carry turns)."""
    kind = record.get("type")
    if kind not in ("user", "assistant"):
        return []
    info.session_id = info.session_id or str(record.get("sessionId", ""))
    info.cwd = info.cwd or str(record.get("cwd", ""))
    info.branch = info.branch or str(record.get("gitBranch", ""))
    if record.get("entrypoint") == "sdk-cli":
        info.skip = info.skip or "headless"
    if record.get("isMeta") or record.get("isSidechain") or record.get("isApiErrorMessage"):
        return []
    if record.get("isCompactSummary"):
        return []
    stamp = str(record.get("timestamp", ""))
    content = (record.get("message") or {}).get("content")
    parts = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
    blocks: list[Block] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            raw = str(part.get("text", ""))
            if kind == "user":
                text = _clean_user_text(raw)
                if not text or text.startswith(_NOISE_USER):
                    continue
                if text.startswith(AUTOLOOP_PROMPT_PREFIX):
                    info.skip = info.skip or "autoloop"
                blocks.append(Block("user", _clip(text, _USER_LIMIT), stamp, end_offset))
            elif raw.strip() and not raw.startswith(_NOISE_ASSISTANT):
                blocks.append(Block("assistant", _clip(raw, _ASSISTANT_LIMIT), stamp, end_offset))
        elif ptype == "tool_use":
            blocks.append(Block("tool", _tool_summary(str(part.get("name", "")), part.get("input")),
                                stamp, end_offset))
        elif ptype == "tool_result" and part.get("is_error"):
            text = _content_text(part.get("content"))
            if text.strip() and "doesn't want to proceed" not in text:
                blocks.append(Block("error", _clip(text, _ERROR_LIMIT), stamp, end_offset))
    return blocks


def codex_blocks(record: dict[str, Any], end_offset: int, info: SessionInfo) -> list[Block]:
    """Blocks from one Codex JSONL record (`response_item` carries turns)."""
    kind = record.get("type")
    payload = record.get("payload") or {}
    stamp = str(record.get("timestamp", ""))
    if kind == "session_meta":
        info.session_id = str(payload.get("id", ""))
        info.cwd = str(payload.get("cwd", ""))
        git = payload.get("git") or {}
        info.branch = str(git.get("branch", "")) if isinstance(git, dict) else ""
        if payload.get("thread_source") == "subagent":
            info.skip = "subagent"
        return []
    if kind != "response_item":
        return []
    ptype = payload.get("type")
    if ptype == "message":
        role = payload.get("role")
        text = _content_text(payload.get("content")).strip()
        if role == "user":
            if text.startswith("<heartbeat>"):
                return [Block("user", HEARTBEAT, stamp, end_offset)]
            if not text or text.startswith(_CODEX_PREAMBLES) or text.startswith(_NOISE_USER):
                return []
            text = _fold_pastes(text)
            if text.startswith(AUTOLOOP_PROMPT_PREFIX):
                info.skip = info.skip or "autoloop"
            return [Block("user", _clip(text, _USER_LIMIT), stamp, end_offset)]
        if role == "assistant" and text.startswith(_IMPORTED):
            info.skip = info.skip or "imported"
            return []
        if role == "assistant" and text and not text.startswith(_NOISE_ASSISTANT):
            return [Block("assistant", _clip(text, _ASSISTANT_LIMIT), stamp, end_offset)]
        return []
    if ptype in ("function_call", "custom_tool_call"):
        args = payload.get("arguments") if ptype == "function_call" else payload.get("input")
        return [Block("tool", _tool_summary(str(payload.get("name", "")), args), stamp, end_offset)]
    return []


def iter_blocks(source: str, data: bytes, start: int, info: SessionInfo) -> Iterator[Block]:
    """Yield blocks for every complete line in `data`, which begins at file offset `start`.

    A trailing partial line (a session still being written) is left for the next read.
    Lines that yield nothing still advance the resume point through a marker block with
    an empty role, so callers can move their cursor past long tool-only stretches.
    """
    parse = claude_blocks if source == "claude" else codex_blocks
    position = 0
    while True:
        newline = data.find(b"\n", position)
        if newline < 0:
            return
        line = data[position:newline]
        position = newline + 1
        end = start + position
        try:
            record = json.loads(line)
        except ValueError:
            yield Block("", "", "", end)
            continue
        produced = parse(record, end, info) if isinstance(record, dict) else []
        yield from produced or [Block("", "", "", end)]
