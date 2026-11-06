"""Check the board's "waiting for you" judgement against what you actually did next.

    python -m kg.plane.waiting_eval [--days 60] [--test-days 20] [--split-at YYYY-MM-DD]
                                    [--no-cache] [--show-errors N --split train|test]

What "waiting" means here (the narrow definition): the agent stopped, and it cannot go on
until you answer it.

The answer key comes from behaviour, never from a model. Every session the board would read in
the last `--days` (same allow list and skip rules as `kg plane collect sessions`) is replayed
block by block. At each point where the agent ended a turn (its last message, then either your
next message or the end of the session) the board's judgement is run on what was there up to
that point, and is compared with what you did next:

- your next message in the same session answers it (`reply_label`): a pick ("1", "选 A"), a
  yes / no lead ("好的", "继续", "不用"), a report that you did what it asked ("弄好了"), or it
  echoes one of the options or the question it asked -> waiting;
- your next message is a long one that does none of that, or a slash command -> not waiting;
- you never came back to it, but opened another session in the same project within 7 days
  -> not waiting;
- anything else (a short message that matches nothing, a paste, nothing happened after) ->
  unknown, left out of the scores.

Known errors of the answer key: a long answer in your own words with no lead word and no echo
counts as "not waiting"; "好，下一步做 X" counts as answering even when the agent had only
offered something; an abandoned question counts as "not waiting" (you showed you did not need
it); a session started in parallel in the same project counts as moving on; answers given
inside an ask tool (AskUserQuestion, ExitPlanMode) are not text replies and are counted apart
(`ask_tool`), not scored.

This is a diagnostic, not the acceptance test. On real sessions the key disagrees with an
independent blind review of the same 20 held-out sessions: it calls "继续 / 可以 / 装好了"
an answer even when the agent asked nothing, and calls a long answer a new task even when it
answers a real question. Scores here are not a grade for the board; the acceptance sample is
`.kg/plane/waiting-review.jsonl` and the blind / human review of those sessions.

Points where you broke in while the agent was still running tools are not turn ends; they are
counted (`interrupted`) and not scored. Scores are split by time: points before `--split-at`
(default: `--test-days` before now) are for tuning, the rest is the test set.

Writes `<vault>/.kg/plane/waiting-eval-<time>.json` (gitignored): counts, scores with 95%
intervals (Wilson; accuracy also by a bootstrap over sessions), the two subsets (the last point
of each session, which is what the board shows; the sessions in `waiting-review.jsonl`) and the
ids of the sessions behind each mistake. No message text goes into it. Replays are cached per
file by size and mtime in `waiting-eval-cache.json` (no text either); the cache is rebuilt when
the judgement or the labelling code changes.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from kg.capture.sessions import HEARTBEAT
from kg.plane import progress
from kg.plane.progress import DAY, WRITING_WINDOW, excerpt, redact, session_state

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from kg.capture.scan import CaptureConfig
    from kg.plane.session_reader import Digest

DEFAULT_DAYS = 60
TEST_DAYS = 20
FOLLOW_UP = 7 * DAY  # a new session in the same project this soon after counts as moving on
LONG_REPLY = 40  # characters; a reply this long that answers nothing is a new task
CACHE = "waiting-eval-cache.json"

# ── the answer key: what your next message says about the agent's last one ──

_LEAD_WORDS = (
    "好的", "好吧", "好嘞", "好滴", "好呀", "好", "行", "可以", "嗯嗯", "嗯", "是的", "是", "对的",
    "对", "没问题", "同意", "批准", "确认", "要的", "要", "需要", "不用", "不要", "不需要", "不必",
    "算了", "先不", "暂时不", "都行", "都可以", "都要", "随便", "按你的", "按你说的", "就这样",
    "照做", "做吧", "开始吧", "开干", "搞吧", "冲", "继续", "ok", "okay", "yes", "yep", "yeah",
    "sure", "no", "nope", "go", "lgtm", "do it", "proceed", "continue", "没有问题", "没啥问题",
    "可以的", "确认无误", "就按这个", "按这个",
)
_LEAD = re.compile(
    r"^(?:" + "|".join(map(re.escape, sorted(_LEAD_WORDS, key=len, reverse=True))) + r")"
    r"(?=$|[\s,，.。!！~～、;；:：…)）]|吧|啊|呀|了|呢|哈|哦|滴|呗)",
    re.I,
)
_NOT_LEAD = re.compile(r"^(?:对了|好像|好多|好几|是不是|要不|行不行|可以吗|好了吗|是否)")
_NUMERAL = "一二三四五六七八九十"
_TOKEN = rf"(?:[0-9]{{1,2}}|[A-Da-d]|[{_NUMERAL}]|第[{_NUMERAL}0-9]+(?:个|种|条|项|步)?)"
_PICK = re.compile(
    rf"^(?:选|就|用|走|按|要)?\s*(?:方案|选项|option)?\s*{_TOKEN}"
    rf"(?:\s*(?:[、,，和&+/与及跟]|and|\s)\s*(?:方案|选项)?\s*{_TOKEN})*"
    r"\s*(?:吧|呗|好了|就行|即可|个|方案|都要|都做|都行|也行)?[\s。.!！~～]*$",
    re.I,
)
# The first clause agrees with what was put to you ("节点规划没问题，但是…").
_AGREES = re.compile(
    r"^[^，,。.!！？?\n]{0,30}?(?:没问题|没有问题|没意见|同意|可以开始|可以了|可以的|"
    r"就这么办|就这样)(?=$|[\s，,。.!！~～：:])"
)
# The first clause reports that what was asked is done ("网络恢复了，…", "登好了").
_DONE = re.compile(
    r"^[^，,。.!！？?\n]{0,24}?(?:好了|完了|完成了|定了|恢复了|成功了|通了|搞定了?|弄好|装好|改好|登好)"
    r"(?=$|[\s，,。.!！~～]|了)|^(?:搞定|done|fixed|finished)\b",
    re.I,
)
_CONTINUE = re.compile(r"^继续")
_COMMAND = re.compile(r"^/[\w:-]+(?=\s|$)")  # "/Users/…" is a path, not a command
# Slash commands that only look after the tool (not a new task, not an answer).
_UPKEEP = re.compile(r"^/(?:compact|model|config|status|cost|login|logout|context|usage|"
                     r"permissions|mcp|doctor|help|resume|rename)\b")
_PASTE = re.compile(r"^〔粘贴")
_OPTION = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*)?\s*(?:(?:方案|选项|option)\s*)?([0-9]{1,2}|[A-Da-d]|[一二三四])"
    r"\s*[.、)）:：]\s*(?:\*\*)?\s*(.+)$",
    re.I | re.M,
)
_MARKUP = re.compile(r"[*`#>_\[\]()（）|]")
_CJK = re.compile(r"[一-鿿]")
_LATIN_ECHO = 10
_OPTION_SPAN = 1500  # how much of the end of the agent's message options are read from


def options(agent: str) -> list[tuple[str, str]]:
    """(label, head of text) of the numbered / lettered items near the end of an agent message."""
    out = []
    for m in _OPTION.finditer(agent[-_OPTION_SPAN:]):
        head = _MARKUP.sub("", m.group(2)).strip()[:24]
        if head:
            out.append((m.group(1).lower(), head))
    return out


def _echoes(source: str, reply: str, shortest: int) -> bool:
    """Whether `reply` repeats a run of `source`: `shortest` characters with a Chinese
    character in them, or `_LATIN_ECHO` for a Latin-only run (shorter ones are mostly product
    names that a new question would repeat too)."""
    text = _MARKUP.sub("", source).lower()
    target = reply.lower()
    for size in (shortest, _LATIN_ECHO):
        for i in range(len(text) - size + 1):
            chunk = text[i:i + size]
            if chunk.strip() != chunk or "\n" in chunk:
                continue
            if (size == shortest) != bool(_CJK.search(chunk)):
                continue
            if chunk in target:
                return True
    return False


def _question_lines(agent: str) -> str:
    lines = [line for line in agent[-_OPTION_SPAN:].splitlines() if line.strip()]
    return "\n".join(line for line in lines[-6:] if "?" in line or "？" in line)


# Text a client wraps around what you typed (Codex attachments, browser state, images).
_MY_REQUEST = re.compile(r"^#+\s*My request(?: for Codex)?:\s*$", re.M)
_WRAPPERS = re.compile(r"<(in-app-browser-context|image|app-context)\b[^>]*>.*?(?:</\1>|\Z)"
                       r"|\[Image #\d+\]|<image[^>]*/?>", re.S)
_NOT_TYPED = ("<codex_internal_context", "<external_", "<bash-input", "<bash-stdout")
_TOOL_ANSWER = "<send_user_message_question_reply"
_NUMBERED_ANSWER = re.compile(r"(?:^|[\s，,；;。])([1-9])\s*[.、)）:：]")
_ANSWER_HEAD = 120  # an answer comes first: echoes are looked for in this much of the reply
_PLAN_PICK = re.compile(
    r"^(?:就|先)?(?:选|采用|用|走|按|要)\s*(?:方案|选项|option)\s*"
    r"[A-Da-d0-9一二三四]", re.I,
)
_LABEL_PICK = re.compile(
    r"^(?:选|方案|选项|第)?\s*([0-9]{1,2}|[A-Da-d])"
    r"(?=$|[\s,，.。、:：)）(（])", re.I,
)


def human_text(reply: str) -> str | None:
    """What you typed, without what the client wrapped around it; None when the message was
    not typed by you (a goal the client re-injects, a shell escape)."""
    text = reply.strip()
    if text.startswith(_NOT_TYPED):
        return None
    if text.startswith("# Files mentioned by the user"):
        mine = _MY_REQUEST.search(text)
        text = text[mine.end():] if mine else ""
    return _WRAPPERS.sub(" ", text).strip()


def reply_label(agent: str, reply: str) -> tuple[bool | None, str]:
    """Did `reply` (your next message) answer `agent` (the agent's last message)?

    True: it answered (picked, said yes / no, reported doing what was asked, or echoed an
    option or the question). False: a slash command, or a long message that does none of that.
    None: cannot tell (a short message that matches nothing, a paste, a scheduled wake-up).
    Returns the rule that decided, for the report.
    """
    if reply.lstrip().startswith(_TOOL_ANSWER):
        return True, "提问工具里回答了"
    typed = human_text(reply)
    if typed is None:
        return None, "不是本人打的字"
    text = typed
    if not text:
        return None, "空发言"
    if _UPKEEP.match(text):
        return None, "维护命令"
    if _COMMAND.match(text):
        return False, "斜杠命令"
    if _PASTE.match(text):
        return None, "粘贴"
    if len(text) <= 20 and _PICK.match(text):
        return True, "选了编号"
    lead = None if _NOT_LEAD.match(text) else _LEAD.match(text)
    # "继续 X 的工作。\n…" running on is a hand-over prompt, not "go on".
    if lead and not (lead.group(0) == "继续" and len(text) > 80):
        return True, "确认或拒绝开头"
    if len(text) <= 20 and _CONTINUE.match(text):
        return True, "继续"
    if _DONE.match(text):
        return True, "回报做完了"
    if _AGREES.match(text):
        return True, "首句认可"
    opts = options(agent)
    numbered = {m.group(1) for m in _NUMBERED_ANSWER.finditer(text)}
    if len(numbered) >= 2 and text.lstrip()[:1] in numbered and (
            {label for label, _ in opts} & numbered or _question_lines(agent).count("\n") >= 1):
        return True, "逐条回答"
    if _PLAN_PICK.match(text):
        return True, "选了编号"
    if opts:
        labels = {label for label, _ in opts}
        picked = _LABEL_PICK.match(text)
        if picked and picked.group(1).lower() in labels:
            return True, "选了编号"
        if any(_echoes(head, text[:_ANSWER_HEAD], 4) for _, head in opts):
            return True, "引用了选项"
    asked = _question_lines(agent)
    if asked and _echoes(asked, text[:_ANSWER_HEAD], 4):
        return True, "回应了问句"
    if len(text) >= LONG_REPLY:
        return False, "长发言，新任务"
    return None, "短发言，看不出"


# ── replaying sessions ──


@dataclass
class Moment:
    """One turn end: what the board would have said then, and what you did next."""

    source: str
    session_id: str
    project: str
    ts: int
    kind: str  # turn (you replied) | end (the session stopped here) | ask_tool | interrupted
    predicted: bool
    reason: str
    label: bool | None = None
    rule: str = ""
    key: str = ""  # same turn in a copied session file (resume / fork) has the same key


@dataclass
class Replay:
    source: str
    file: str
    size: int
    mtime: float
    session_id: str = ""
    project: str = ""
    skip: str = ""
    first_user: int | None = None
    moments: list[Moment] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Replay:
        moments = [Moment(**m) for m in raw.pop("moments", [])]
        return cls(**raw, moments=moments)


@dataclass
class _Point:
    """The agent's side of a turn end, kept in memory only while the session is read."""

    ts: int
    predicted: bool
    reason: str
    last_event: str
    agent: str
    key: str
    opener: bool  # your message before it was only "hi" and the like


def judged_waiting(digest: Digest) -> tuple[bool, str]:
    """The board's judgement on a digest, as if it had stopped being written."""
    from kg.plane.collectors.sessions import waiting_reason

    state = session_state(last_event=digest.last_event, last_question=digest.last_question,
                          mtime=0, now=WRITING_WINDOW + 1)
    return state == "waiting", waiting_reason(digest) if state == "waiting" else ""


def replay_file(source: str, file: Path, *, project_of: Callable[[str], str | None],
                keep_text: dict[str, tuple[str, str]] | None = None) -> Replay:
    """Read one session file from the start and collect its turn ends.

    `keep_text`, when given, receives {moment key: (agent message, your reply)} for local
    error review; nothing else keeps text.
    """
    from kg.plane.collectors.sessions import _OPENER, is_trivial
    from kg.plane.session_reader import _TRUNCATED, _epoch, _raw_text, read_session

    stat = file.stat()
    rep = Replay(source, str(file), stat.st_size, stat.st_mtime)
    state: dict[str, Any] = {"agent": "", "user": "", "since_user": False}
    pending: list[tuple[_Point, str | None, str]] = []  # point, reply text (None = end), kind

    def point(d: Digest) -> _Point:
        predicted, reason = judged_waiting(d)
        ts = int(d.last_agent_ts or d.last_ts or 0)
        digest_key = f"{source}|{ts}|{state['agent'][:200]}".encode()
        return _Point(ts, predicted, reason, d.last_event, state["agent"],
                      hashlib.sha1(digest_key).hexdigest()[:16],
                      bool(_OPENER.match(state["user"].strip())))

    def observe(role: str, text: str, stamp: str, raw: Callable[[], bytes], d: Digest) -> None:
        if role == "user":
            ts = _epoch(stamp)
            if rep.first_user is None and ts is not None:
                rep.first_user = ts
            if state["since_user"]:
                p = point(d)
                if p.last_event in ("assistant", "ask"):
                    pending.append((p, text, "turn"))
                elif p.last_event in ("tool", "error"):
                    pending.append((p, None, "interrupted"))
            state["since_user"] = False
            state["user"] = text
        elif role == "assistant":
            full = _raw_text(source, raw()) if text.endswith(_TRUNCATED) else text
            state["agent"] = full.strip() or text
            if d.last_event == "ask":  # the ask tool was answered, the agent went on
                pending.append((point(d), None, "ask_tool"))
            state["since_user"] = True
        elif role == "tool":
            if d.last_event == "ask":
                pending.append((point(d), None, "ask_tool"))
            state["since_user"] = True

    def allowed(cwd: str) -> bool:
        return project_of(cwd) is not None

    digest = read_session(source, file, None, allowed=allowed, observe=observe)
    rep.session_id, rep.skip = digest.session_id, digest.skip
    project = project_of(digest.cwd) if not digest.skip else None
    if not project:
        rep.skip = rep.skip or "not-allowed"
        return rep
    if is_trivial(digest):
        rep.skip = "trivial"
        return rep
    rep.project = project
    if state["since_user"] and digest.last_event in ("assistant", "ask"):
        pending.append((point(digest), None, "end"))
    for p, reply, kind in pending:
        m = Moment(source, rep.session_id, project, p.ts, kind, p.predicted, p.reason, key=p.key)
        if kind == "turn":
            if reply == HEARTBEAT:
                m.label, m.rule = None, "定时触发"
            elif p.opener:
                m.label, m.rule = None, "开场寒暄"
            else:
                m.label, m.rule = reply_label(p.agent, reply or "")
        elif kind == "ask_tool":
            m.label, m.rule = True, "提问工具里回答了"
        rep.moments.append(m)
        if keep_text is not None and kind in ("turn", "end"):
            keep_text[p.key] = (p.agent, reply or "")
    return rep


def _fingerprint() -> str:
    from kg.plane import session_reader

    parts = [inspect.getsource(progress), inspect.getsource(session_reader),
             inspect.getsource(sys.modules[__name__])]
    return hashlib.sha1("".join(parts).encode()).hexdigest()[:16]


def _files(config: CaptureConfig, since: float) -> Iterable[tuple[str, Path]]:
    from kg.plane.collectors.sessions import _session_files

    for source, file, _ in _session_files(config, since):
        yield source, file


def replay_all(config: CaptureConfig, *, now: int, days: int, cache_file: Path | None,
               keep_text: dict[str, tuple[str, str]] | None = None) -> list[Replay]:
    """Replays of every session file changed in the last `days`; unchanged files come from the
    cache (by size and mtime) unless text is wanted."""
    from kg.capture.scan import project_for

    def project_of(cwd: str) -> str | None:
        return project_for(cwd, config)

    stamp = _fingerprint()
    cached: dict[str, Replay] = {}
    if cache_file is not None and cache_file.exists() and keep_text is None:
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            if raw.get("version") == stamp:
                cached = {k: Replay.from_json(v) for k, v in raw.get("files", {}).items()}
        except (ValueError, TypeError):
            cached = {}
    out: list[Replay] = []
    fresh: dict[str, Replay] = {}
    for source, file in _files(config, now - days * DAY):
        key = str(file)
        try:
            stat = file.stat()
            old = cached.get(key)
            if old and old.size == stat.st_size and old.mtime == stat.st_mtime:
                rep = old
            else:
                rep = replay_file(source, file, project_of=project_of, keep_text=keep_text)
        except OSError:
            continue
        fresh[key] = rep
        out.append(rep)
    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_name(f".{cache_file.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"version": stamp,
                                   "files": {k: r.to_json() for k, r in fresh.items()}}),
                       encoding="utf-8")
        tmp.replace(cache_file)
    return out


def label_ends(replays: list[Replay], *, now: int) -> list[Moment]:
    """All moments, copies merged, with the sessions that just stopped labelled by what came
    next in the same project."""
    moments: dict[str, Moment] = {}
    for rep in replays:
        if rep.skip:
            continue
        for m in rep.moments:
            seen = moments.get(m.key)
            # A copied session holds the same turn; keep the copy where you replied.
            if seen is None or (seen.kind == "end" and m.kind != "end"):
                moments[m.key] = m
    starts: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for rep in replays:
        if not rep.skip and rep.first_user is not None:
            starts[rep.project].append((rep.first_user, rep.session_id))
    for m in moments.values():
        if m.kind != "end":
            continue
        later = [s for s, sid in starts[m.project] if sid != m.session_id and m.ts < s
                 <= m.ts + FOLLOW_UP]
        if later:
            m.label, m.rule = False, "同一项目另开了会话"
        elif now - m.ts < FOLLOW_UP:
            m.label, m.rule = None, "还在观察期"
        else:
            m.label, m.rule = None, "之后什么都没发生"
    return sorted(moments.values(), key=lambda m: m.ts)


# ── scores ──


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def _ratio(k: int, n: int) -> float | None:
    return round(k / n, 3) if n else None


def scores(moments: list[Moment], *, boot: int = 1000, seed: int = 5) -> dict[str, Any]:
    """Precision, recall and accuracy of `predicted` against `label` on the labelled moments."""
    judged = [m for m in moments if m.label is not None]
    tp = sum(1 for m in judged if m.predicted and m.label)
    fp = sum(1 for m in judged if m.predicted and not m.label)
    fn = sum(1 for m in judged if not m.predicted and m.label)
    tn = len(judged) - tp - fp - fn
    out: dict[str, Any] = {
        "labelled": len(judged), "unknown": len(moments) - len(judged),
        "sessions": len({(m.source, m.session_id) for m in judged}),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": _ratio(tp, tp + fp), "precision_ci": wilson(tp, tp + fp),
        "recall": _ratio(tp, tp + fn), "recall_ci": wilson(tp, tp + fn),
        "accuracy": _ratio(tp + tn, len(judged)), "accuracy_ci": wilson(tp + tn, len(judged)),
    }
    # Turn ends of one session are not independent: resample whole sessions as well.
    by_session: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for m in judged:
        by_session[(m.source, m.session_id)].append(m.predicted == m.label)
    groups = list(by_session.values())
    if groups and boot:
        rng = random.Random(seed)
        accs = []
        for _ in range(boot):
            pick = [groups[rng.randrange(len(groups))] for _ in groups]
            right = sum(sum(g) for g in pick)
            total = sum(len(g) for g in pick)
            accs.append(right / total)
        accs.sort()
        out["accuracy_ci_by_session"] = [round(accs[int(0.025 * boot)], 3),
                                         round(accs[int(0.975 * boot) - 1], 3)]
    return out


def mistakes(moments: list[Moment]) -> dict[str, list[str]]:
    def ids(pred: bool) -> list[str]:
        return sorted({f"{m.source}:{m.session_id}" for m in moments
                       if m.label is not None and m.predicted == pred and m.label != pred})

    return {"false_waiting": ids(True), "missed_waiting": ids(False)}


def last_points(moments: list[Moment]) -> list[Moment]:
    """The last turn end of each session: the one the board shows."""
    last: dict[tuple[str, str], Moment] = {}
    for m in moments:
        if m.kind in ("turn", "end"):
            k = (m.source, m.session_id)
            if k not in last or m.ts >= last[k].ts:
                last[k] = m
    return list(last.values())


def review_points(moments: list[Moment], review: list[dict[str, Any]], *, year: int
                  ) -> tuple[list[Moment], dict[str, Any]]:
    """For each session of an earlier hand review: the turn end the review looked at (the last
    one at or before its `last_active`), plus how the blind model review fared on the same
    behavioural labels."""
    by_session: dict[str, list[Moment]] = defaultdict(list)
    for m in moments:
        if m.kind in ("turn", "end"):
            by_session[f"{m.source}:{m.session_id}"].append(m)
    picked: list[Moment] = []
    model_right = model_judged = 0
    missing = 0
    for line in review:
        points = by_session.get(line.get("key", ""))
        if not points:
            missing += 1
            continue
        try:
            seen = datetime.strptime(f"{year}-{line['last_active']}", "%Y-%m-%d %H:%M")
            limit = int(seen.timestamp()) + 59
        except (KeyError, ValueError):
            limit = None
        before = [m for m in points if limit is None or m.ts <= limit]
        m = max(before or points, key=lambda x: x.ts)
        picked.append(m)
        if m.label is not None and isinstance(line.get("model"), bool):
            model_judged += 1
            model_right += line["model"] == m.label
    extra = {"sessions": len(review), "not_found": missing,
             "model_review_vs_behaviour": {"judged": model_judged, "agree": model_right}}
    return picked, extra


def report(moments: list[Moment], *, split_at: int, since: int, review: list[dict[str, Any]],
           now: int) -> dict[str, Any]:
    window = [m for m in moments if m.ts >= since]
    scored = [m for m in window if m.kind in ("turn", "end")]
    train = [m for m in scored if m.ts < split_at]
    test = [m for m in scored if m.ts >= split_at]
    last = last_points(window)
    year = datetime.fromtimestamp(now).year
    picked, review_extra = review_points(window, review, year=year)
    kinds = Counter(m.kind for m in window)
    rules = Counter(m.rule for m in scored)
    unknown_by_prediction = Counter(("waiting" if m.predicted else "not")
                                    for m in scored if m.label is None)
    ask = [m for m in window if m.kind == "ask_tool"]
    return {
        "generated_at": now, "since": since, "split_at": split_at,
        "definition": "在等你 = Agent 停下来了，本人不回它就没法继续",
        "caveat": "诊断用：标签由本人下一句倒推，与留出会话的独立盲审不一致；"
                  "不作为验收分数，验收看 waiting-review.jsonl 的盲审 / 人工复核",
        "moments": dict(kinds), "label_rules": dict(rules),
        "unknown_by_prediction": dict(unknown_by_prediction),
        "train": scores(train), "test": scores(test),
        "test_mistakes": mistakes(test),
        "last_point": {"test": scores([m for m in last if m.ts >= split_at]),
                       "all": scores(last)},
        "review_sessions": {**review_extra, "scores": scores(picked),
                            "mistakes": mistakes(picked)},
        "ask_tool": {"moments": len(ask), "judged_waiting": sum(m.predicted for m in ask)},
    }


# ── command ──


def _vault() -> Path:
    from kg.infrastructure.config.loader import load_config

    return load_config().vault_path


def _date(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").timestamp())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kg.plane.waiting_eval")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--test-days", type=int, default=TEST_DAYS)
    parser.add_argument("--split-at", default=None,
                        help="测试集起点 YYYY-MM-DD（默认今天往前 --test-days 天）")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--show-errors", type=int, default=0,
                        help="在终端列出 N 个判错的时刻（脱敏、截断，只在本机看，不写进报告）")
    parser.add_argument("--split", choices=["train", "test"], default="train")
    args = parser.parse_args(argv)

    from kg.capture.scan import load_capture_config
    from kg.plane.waiting_review import review_path

    vault = _vault()
    now = int(time.time())
    since = now - args.days * DAY
    split_at = _date(args.split_at) if args.split_at else now - args.test_days * DAY
    config = load_capture_config(vault)
    texts: dict[str, tuple[str, str]] | None = {} if args.show_errors else None
    cache = None if args.no_cache else vault / ".kg" / "plane" / CACHE
    replays = replay_all(config, now=now, days=args.days, cache_file=cache, keep_text=texts)
    moments = label_ends(replays, now=now)
    review: list[dict[str, Any]] = []
    rpath = review_path(vault)
    if rpath.exists():
        review = [json.loads(line) for line in rpath.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
    out = report(moments, split_at=split_at, since=since, review=review, now=now)
    path = vault / ".kg" / "plane" / f"waiting-eval-{time.strftime('%Y%m%d-%H%M')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    t = out["test"]
    print(f"测试集 {t['labelled']} 个有标签的时刻（{t['sessions']} 个会话）："
          f"准确率 {t['accuracy']} {t['accuracy_ci']}，精确率 {t['precision']}，"
          f"召回率 {t['recall']}。报告 {path}")
    if texts:
        side = [m for m in moments if m.ts >= since and m.kind in ("turn", "end")
                and (m.ts < split_at) == (args.split == "train")]
        wrong = [m for m in side if m.label is not None and m.label != m.predicted]
        for m in wrong[:args.show_errors]:
            agent, reply = texts.get(m.key, ("", ""))
            print("\n──", "误判在等" if m.predicted else "漏判", m.source, m.session_id, m.rule,
                  m.reason)
            print("Agent 结尾：", excerpt(redact(agent), 300, tail=True))
            print("你的下一句：", excerpt(redact(reply), 160))
    return 0


if __name__ == "__main__":
    sys.exit(main())
