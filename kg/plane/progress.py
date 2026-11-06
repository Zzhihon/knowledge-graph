"""Pure judgements behind the in-progress board; no files, no commands, no model.

- `redact` / `excerpt`  what of a session may be shown: links, credentials, IPs taken out
                        (the same credential / link patterns `kg eval mine` refuses), then clipped.
- `is_question`         does an agent reply end by asking the user something.
- `session_state`       working / waiting / done / interrupted for one session.
- `judge_project`       active / waiting / stalled / idle / done for one project.
- parsers for `git status --porcelain=v2 --branch`, `orca worktree ps --json`,
  `orca project list --json` and the Current State of a knowledge-base project page.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from typing import Any

from kg.eval.retrieval import _SECRETISH

MINUTE = 60
HOUR = 3600
DAY = 86400
WRITING_WINDOW = 10 * MINUTE  # file changed this recently: still being written
ACTIVE_WINDOW = HOUR  # project moved this recently: active
WAITING_WINDOW = 3 * DAY  # an older unanswered question no longer makes a project "waiting"
STALL_AFTER = 3 * DAY  # no movement this long while work is left open: stalled

REDACTED = "〔已隐去〕"
_URL = re.compile(r"https?://\S+")
_ASSIGNED = re.compile(
    r"(?i:api[_ -]?key|secret|token|password|passwd|密钥|密码|口令)\s*(?:[:：=]|是|为)\s*\S+"
)
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_SPACES = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n\s*\n+")


def redact(text: str) -> str:
    """Remove links, credential-looking strings (and the value after `token:` etc.) and IPv4s."""
    text = _URL.sub("〔链接〕", text)
    text = _ASSIGNED.sub(REDACTED, text)
    text = _IPV4.sub("〔IP〕", text)
    return _SECRETISH.sub(REDACTED, text)


def excerpt(text: str, limit: int, *, tail: bool = False) -> str:
    """Whitespace squeezed, then the first (or with `tail`, the last) `limit` characters."""
    text = _BLANKS.sub("\n", _SPACES.sub(" ", text.replace("\r", ""))).strip()
    if len(text) <= limit:
        return text
    return "…" + text[-limit:].lstrip() if tail else text[:limit].rstrip() + "…"


# ── is the agent asking? ──

# Question marks count only near the end: a reply that asked something halfway and then went
# on to report results is not waiting.
_TAIL_CHARS = 360
_ENDS_ASKING = re.compile(
    r"[?？]\s*(?:[)）\]】*_`\"'”’]|[\U0001F300-\U0001FAFF\u2600-\u27BF])*\s*$"
)
_ASKS = re.compile(
    r"要不要|需不需要|是否(?:要|需要|继续|同意|可以|先|现在|由你|让我)|"
    r"你(?:想|要不要|希望|看|觉得|决定|来定|选|拍板|确认|批准|同意)|"
    r"请(?:你)?(?:确认|选择|决定|回复|告诉我|拍板|批准|过目|提供|给我)|"
    r"等你(?:的)?(?:确认|决定|回复|回答|批准|拍板|选择|指示|消息)|"
    r"需要你(?:先|来)?(?:确认|决定|拍板|选|回答|提供|批准|同意|给|做|手动|登录|授权|处理)|"
    r"(?:等|待)你(?:来)?处理|直接说要做什么|"
    r"(?:你|您)(?:拍板|定一下|说一声|回个)|(?:可以|行|好)吗|选哪|哪(?:个|种|一个)(?:更|好|合适)|"
    r"(?i:\bshould i\b|\bdo you want\b|\bwould you like\b|\bshall i\b|"
    r"\bplease confirm\b|\bwhich (?:one|option)\b|\bwaiting for (?:your|you)\b)"
)
# "Tell me when it's done" asks the user to act; "tell me if you want more" is only an offer.
_TELL_ME = re.compile(r"(?:发我|告诉我|跟我说|贴给我|回我|说一声|(?i:\blet me know\b|\btell me\b))")
_OPTIONAL = re.compile(r"的话|如果|若|需要的话|想要?|有需要|可以再|(?i:\bif\b)")
_ASK_HEADINGS = re.compile(
    r"^\s*(?:#+\s*)?(?:\*\*)?(?:需要你(?:来)?(?:拍板|决定|确认|做|处理)|你需要(?:决定|拍板|确认|做)|"
    r"待你(?:拍板|决定|确认|处理)|等你(?:拍板|决定|确认|处理)|请你(?:决定|确认)|需要拍板|待拍板|"
    r"决策点|要你决定)",
    re.M,
)
# Asking the user to do something by hand before the agent can go on.
_HAND_OVER = re.compile(
    r"(?:建议|需要|请|得)(?:你|您)(?:自己|手动|本人|亲自)|(?i:\brun it yourself\b)"
)
# A short reply (a greeting, a clarifying question) that puts a question to the user anywhere.
_SHORT_REPLY = 500
_QUESTION_TO_USER = re.compile(r"[^。！？!?\n]*(?:你|您|(?i:\byou\b))[^。！？!?\n]*[?？]")
# Machine notes some agents append after the reply (memory citations and the like).
_TRAILER = re.compile(r"<(oai-mem-citation|citation_entries|rollout_ids)>.*?(?:</\1>|\Z)", re.S)


def question_reason(text: str) -> str:
    """Why an agent reply counts as asking the user ("" when it does not).

    Counts: a question at the very end; a "需要你决定 / 等你处理" heading near the end; a short
    reply that puts a question to the user anywhere; an ask phrase in the last three lines;
    "做完后告诉我" style requests; "建议你自己确认后再做" style hand-overs. Does not count
    offers ("需要的话告诉我"), or a question asked halfway through a long reply that then went on.
    """
    body = _TRAILER.sub(" ", _FENCE.sub(" ", text)).strip()
    if not body:
        return ""
    tail = body[-_TAIL_CHARS:]
    if _ENDS_ASKING.search(body):
        return "结尾是问句"
    heading = _ASK_HEADINGS.search(tail)
    if heading:
        return f"有小标题「{heading.group(0).strip().strip('#* ')}」"
    if len(body) <= _SHORT_REPLY and _QUESTION_TO_USER.search(body):
        return "回复很短，里面在问你"
    last = [line for line in tail.splitlines() if line.strip()][-3:]
    for line in last:
        ask = _ASKS.search(line)
        if ask:
            return f"最后几行在问你：「{ask.group(0)}」"
    for line in last:
        tell = _TELL_ME.search(line)
        if tell and not _OPTIONAL.search(line):
            return f"要你动手后回话：「{tell.group(0)}」"
    for line in body[-4 * _TAIL_CHARS:].splitlines():
        hand = _HAND_OVER.search(line)
        if hand and not _OPTIONAL.search(line):
            return f"要你自己动手：「{hand.group(0)}」"
    return ""


def is_question(text: str) -> bool:
    """Whether an agent reply ends by asking the user to answer, choose, confirm, approve or act."""
    return bool(question_reason(text))


# ── session and project stages ──

SESSION_STATES = ("working", "waiting", "done", "interrupted")


def session_state(*, last_event: str, last_question: bool, mtime: float, now: int) -> str:
    """working: written in the last 10 minutes and not stopped on a question; waiting: the
    agent's last move is a question (or a pending ask tool); done: it ended on a plain reply;
    interrupted: it stopped mid-turn (last thing is the user or a tool call) and went quiet."""
    asking = last_event == "ask" or (last_event == "assistant" and last_question)
    if asking:
        return "waiting"
    if now - mtime <= WRITING_WINDOW:
        return "working"
    return "done" if last_event == "assistant" else "interrupted"


STAGES = ("waiting", "active", "stalled", "idle", "done")
STAGE_LABELS = {"waiting": "等你", "active": "活跃", "stalled": "停滞", "idle": "歇着",
                "done": "已收尾"}
STAGE_STATUS = {"waiting": "degraded", "active": "up", "stalled": "degraded", "idle": "asleep",
                "done": "asleep"}


@dataclass(frozen=True)
class Stage:
    stage: str
    reason: str
    last_action: int | None


def judge_project(*, now: int, sessions: list[dict[str, Any]], repos: list[dict[str, Any]],
                  open_prs: int | None, other_activity: int | None = None) -> Stage:
    """Stage of one project from its sessions (`state`, `last_active`), repos (`dirty`,
    `last_commit`), open PR count (None = unknown) and any other last-activity time (such as
    an Orca terminal's last output).

    In order: waiting (a session stopped on a question in the last 3 days); active (anything
    moved in the last hour, or an agent is writing right now); stalled (nothing moved for 3
    days but there are uncommitted changes or open PRs); done (nothing left open); otherwise
    idle (moved within 3 days, work still open).
    """
    times = [s["last_active"] for s in sessions if s.get("last_active")]
    times += [r["last_commit"] for r in repos if r.get("last_commit")]
    times += [other_activity] if other_activity else []
    last = max(times) if times else None
    dirty = sum(int(r.get("dirty") or 0) for r in repos)
    waiting = [s for s in sessions if s.get("state") == "waiting"
               and now - (s.get("last_active") or 0) <= WAITING_WINDOW]
    if waiting:
        return Stage("waiting", f"{len(waiting)} 个会话在等你回答", last)
    if any(s.get("state") == "working" for s in sessions):
        return Stage("active", "有会话正在写", last)
    if last is not None and now - last <= ACTIVE_WINDOW:
        return Stage("active", "1 小时内有动作", last)
    open_bits = ([f"{dirty} 处未提交改动"] if dirty else []) + (
        [f"{open_prs} 个未合并 PR"] if open_prs else [])
    if last is not None and now - last >= STALL_AFTER and open_bits:
        days = (now - last) // DAY
        return Stage("stalled", f"{days} 天没动，{'、'.join(open_bits)}", last)
    if not open_bits:
        return Stage("done", "没有未提交改动和未合并 PR", last)
    return Stage("idle", f"3 天内动过，{'、'.join(open_bits)}", last)


# ── git ──


@dataclass
class GitStatus:
    branch: str = ""
    upstream: str = ""
    ahead: int | None = None
    behind: int | None = None
    changed: int = 0  # tracked files with staged or unstaged changes
    untracked: int = 0
    conflicts: int = 0

    @property
    def dirty(self) -> int:
        return self.changed + self.untracked + self.conflicts


def parse_git_status(text: str) -> GitStatus:
    """`git status --porcelain=v2 --branch`."""
    out = GitStatus()
    for line in text.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):].strip()
            out.branch = "" if head == "(detached)" else head
        elif line.startswith("# branch.upstream "):
            out.upstream = line[len("# branch.upstream "):].strip()
        elif line.startswith("# branch.ab "):
            parts = line.split()
            with contextlib.suppress(IndexError, ValueError):
                out.ahead, out.behind = int(parts[2].lstrip("+")), abs(int(parts[3]))
        elif line.startswith(("1 ", "2 ")):
            out.changed += 1
        elif line.startswith("u "):
            out.conflicts += 1
        elif line.startswith("? "):
            out.untracked += 1
    return out


_REMOTE = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.I)


def github_repo(remote_url: str) -> str:
    """`owner/name` of a GitHub remote URL, lowercased; "" for anything else."""
    m = _REMOTE.search(remote_url.strip())
    return f"{m.group(1)}/{m.group(2)}".lower() if m else ""


# ── orca ──


@dataclass
class OrcaWorktree:
    path: str
    repo: str
    repo_id: str
    branch: str
    status: str  # working | active | inactive (as Orca reports it)
    live: int
    unread: bool
    last_output: int | None  # Unix seconds
    last_activity: int | None
    agents: list[dict[str, str]] = field(default_factory=list)  # [{type, state}]


def _seconds(ms: Any) -> int | None:
    return int(ms) // 1000 if isinstance(ms, (int, float)) and ms > 0 else None


def parse_orca_ps(raw: dict[str, Any]) -> list[OrcaWorktree]:
    """`orca worktree ps --json`. Previews and prompts are dropped: they echo terminal text."""
    result = raw.get("result") if isinstance(raw.get("result"), dict) else raw
    out: list[OrcaWorktree] = []
    for w in result.get("worktrees") or []:
        if not isinstance(w, dict) or w.get("isArchived") or not w.get("path"):
            continue
        branch = str(w.get("branch") or "")
        branch = branch[len("refs/heads/"):] if branch.startswith("refs/heads/") else branch
        agents = [{"type": str(a.get("agentType", "")), "state": str(a.get("state", ""))}
                  for a in w.get("agents") or [] if isinstance(a, dict)]
        out.append(OrcaWorktree(
            path=str(w["path"]), repo=str(w.get("repo") or ""), repo_id=str(w.get("repoId") or ""),
            branch=branch, status=str(w.get("status") or ""),
            live=int(w.get("liveTerminalCount") or 0), unread=bool(w.get("unread")),
            last_output=_seconds(w.get("lastOutputAt")),
            last_activity=_seconds(w.get("lastActivityAt")), agents=agents,
        ))
    return out


def parse_orca_projects(raw: dict[str, Any]) -> dict[str, str]:
    """`orca project list --json` -> {repo id: "owner/name"} for GitHub-backed projects."""
    result = raw.get("result") if isinstance(raw.get("result"), dict) else raw
    out: dict[str, str] = {}
    for p in result.get("projects") or []:
        ident = (p or {}).get("providerIdentity") or {}
        if ident.get("provider") != "github" or not ident.get("owner") or not ident.get("repo"):
            continue
        for repo_id in p.get("sourceRepoIds") or []:
            out[str(repo_id)] = f"{ident['owner']}/{ident['repo']}".lower()
    return out


# ── knowledge-base project pages ──

_CALLOUT_HEAD = re.compile(r"^\[!\w+\][-+]?\s*(.*)$")
_STAGE_LINE = re.compile(r"\*\*阶段\*\*\s*[:：]\s*(.+)")
_SENTENCE_END = re.compile(r"(?<=[。！？!?])")


def current_state(markdown: str, limit: int = 140) -> tuple[str, str]:
    """(one sentence, "as of" note) from a project page's `## Current State` section.

    The sentence is the `**阶段**：` line when there is one, else the section's first line of
    text; the note is the callout title (e.g. "当前状态（截至 10-27 会话）").
    """
    lines = markdown.splitlines()
    try:
        start = next(i for i, line in enumerate(lines)
                     if re.match(r"^##\s+(Current State|当前状态)\b", line.strip()))
    except StopIteration:
        return "", ""
    body: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## ") or line.startswith("```"):
            break
        body.append(line.lstrip("> ").strip())
    note = ""
    text_lines = []
    for line in body:
        head = _CALLOUT_HEAD.match(line)
        if head:
            note = head.group(1).strip()
            continue
        if line:
            text_lines.append(line)
    chosen = next((m.group(1) for m in map(_STAGE_LINE.search, text_lines) if m), None)
    if chosen is None:
        chosen = text_lines[0] if text_lines else ""
    chosen = re.sub(r"\*\*|`", "", chosen).strip()
    first = next((s for s in _SENTENCE_END.split(chosen) if s.strip()), chosen).strip()
    return excerpt(redact(first), limit), note
