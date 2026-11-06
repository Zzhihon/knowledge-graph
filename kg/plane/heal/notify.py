"""`kg plane notify --autoloop`: incident events in, cards and heal actions out.

The agent-autoloop `command` agent gets its round's context as a file (AGENT_AUTOLOOP_CONTEXT):
`events` (plane.incident.opened / closed from the collectors), `answers` (what the owner picked
on an earlier card), `timers` (re-checks that came due) and `open` (cards still open). This
module turns that into one receipt:

    opened  → heal actions (suggest / auto) → an `ask` card: 已处理 / 忽略一周 / 交给 Agent 查
              (+ "照建议做" when there is a suggestion; a `notice` + re-check timer instead when
              an automatic restart is being tried). Silenced, muted and info incidents get none.
    closed  → undo what the incident needed (resume, catch up, mark fresh) → a `notice`
              "已恢复，持续 N 分钟", `resolve` of its open card, and `incident.retro.ready`
              when the incident deserves a retro
    answers → 忽略一周 writes silence.json; 交给 Agent 查 emits plane.incident.investigate;
              照建议做 runs the suggested action
    timers  → still down after an automatic restart: escalate to an `ask` card

Each incident is announced once: `.kg/plane/notified.json` remembers which round (run number)
sent its card. A later round skips it; the same round run again (the hub re-dispatching after
a failure) builds the same receipt, so a lost result does not lose the card. The hub's `key` on
cards dedups on its side too.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from kg.plane.heal import actions, audit, silence
from kg.plane.heal.audit import DONE
from kg.plane.heal.config import plane_dir
from kg.plane.heal.retro import (
    TZ,
    build_retro_input,
    entry_ref,
    get_incident,
    incident_dir,
    previous_retro,
    retro_event,
    scrub_text,
    should_retro,
    write_retro_input,
)
from kg.plane.store import get_resource

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable
    from pathlib import Path

    from kg.plane.heal.config import HealConfig
    from kg.plane.heal.hub_actions import HubActions

_EVENT_ID = re.compile(r"^plane-incident-(\d+)-(opened|closed)$")
HANDLED, SILENCE, INVESTIGATE = "已处理", "忽略一周", "交给 Agent 查"
SUGGEST_PREFIX = "照建议做"
INVESTIGATE_EVENT = "plane.incident.investigate"
NOTIFIED_CARDS = ("ask", "notice", "skipped-closed")


def notified_path(vault: Path) -> Path:
    return plane_dir(vault) / "notified.json"


def _load(vault: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(notified_path(vault).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(vault: Path, data: dict[str, dict[str, Any]]) -> None:
    file = notified_path(vault)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, file)


def card_tag(incident_id: int) -> str:
    return f"[事故 #{incident_id}]"


def _hm(ts: int | None) -> str:
    return "—" if ts is None else datetime.fromtimestamp(ts, TZ).strftime("%m-%d %H:%M")


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds")


def minutes(seconds: int) -> int:
    return max(1, round(seconds / 60))


@dataclass
class Receipt:
    ask: list[dict[str, Any]] = field(default_factory=list)
    notice: list[dict[str, Any]] = field(default_factory=list)
    resolve: list[dict[str, Any]] = field(default_factory=list)
    later: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # what happened, for the conclusion line

    def empty(self) -> bool:
        return not (self.ask or self.notice or self.resolve or self.later or self.events)

    def body(self) -> dict[str, Any]:
        return {k: v for k, v in (("ask", self.ask), ("notice", self.notice),
                                  ("resolve", self.resolve), ("later", self.later),
                                  ("events", self.events)) if v}

    def render(self) -> str:
        """What the command prints: one conclusion line, then the receipt block."""
        if self.empty():
            return "没事可做：" + ("；".join(self.lines) or "没有要通知的事故")
        head = "；".join(self.lines) or "事故通知"
        block = json.dumps(self.body(), ensure_ascii=False)
        return f"{head}\n\n```agent-autoloop\n{block}\n```"


@dataclass
class Notifier:
    conn: sqlite3.Connection
    vault: Path
    config: HealConfig
    now: int
    run: str = ""  # the hub's run number (AGENT_AUTOLOOP_RUN); a retry of one round shares it
    hub: HubActions | None = None
    runner: Callable[..., str] | None = None  # launchctl / osascript
    receipt: Receipt = field(default_factory=Receipt)

    def __post_init__(self) -> None:
        self.state = _load(self.vault)
        self.heal = actions.HealContext(self.vault, self.config, self.now, self.hub, self.runner)

    # ── bookkeeping ──

    def _slot(self, incident_id: int) -> dict[str, Any]:
        return self.state.setdefault(str(incident_id), {})

    def _done_before(self, incident_id: int, step: str) -> bool:
        """Whether an earlier round already handled this step (a retry of this round has not)."""
        seen = self._slot(incident_id).get(step)
        return bool(seen) and seen.get("run") != self.run

    def _mark(self, incident_id: int, step: str, **info: Any) -> None:
        self._slot(incident_id)[step] = {"run": self.run, "at": self.now, **info}

    # ── cards ──

    def _base_text(self, inc: dict[str, Any], resource: dict[str, Any] | None) -> list[str]:
        name = (resource or {}).get("name") or ""
        lines = [f"资源：{inc['resource_id']}" + (f"（{name}）" if name and name not in
                                                  inc["resource_id"] else ""),
                 f"规则：{inc['rule']} · {inc['severity']} · {_hm(inc['opened_at'])} 开始"]
        started = (inc.get("evidence") or {}).get("started_at")
        if started and started != inc["opened_at"]:
            lines.append(f"第一次失败：{_hm(started)}")
        return lines

    def _previous(self, inc: dict[str, Any]) -> str | None:
        prev = previous_retro(self.conn, inc)
        if prev is None:
            return None
        return f"上次同类事故 #{prev['id']}（{_hm(prev['opened_at'])}）的复盘：" \
               f"{entry_ref(self.vault, prev['entry_id'])}"

    def _ask(self, inc: dict[str, Any], resource: dict[str, Any] | None,
             outcomes: list[actions.Outcome], *, title: str, key: str) -> None:
        text = [scrub_text(t) for t in self._base_text(inc, resource) + [o.text for o in outcomes]]
        prev = self._previous(inc)
        if prev:  # entry ids are checked on link; the scrubber would mask the long path
            text.append(prev)
        options = [HANDLED, SILENCE, INVESTIGATE]
        suggestion = next((o.option for o in outcomes if o.option), None)
        if suggestion:
            options.append(suggestion)
        self.receipt.ask.append({
            "title": scrub_text(title), "text": "\n".join(text),
            "options": options, "recommend": len(options) - 1 if suggestion else 0,
            "about": f"incident:{inc['id']}", "key": key,
        })
        self._desktop(title)

    def _notice(self, title: str, lines: list[str], key: str) -> None:
        self.receipt.notice.append({"title": scrub_text(title),
                                    "text": scrub_text("\n".join(lines)), "key": key})

    def _desktop(self, title: str) -> None:
        if not self.config.desktop:
            return
        run = self.runner
        if run is None:
            from kg.plane.collectors import run_command as run
        try:  # the text goes in as an argument, never into the script
            run(["osascript", "-e", "on run argv", "-e",
                 "display notification (item 2 of argv) with title (item 1 of argv)",
                 "-e", "end run", "--", "管控面事故", scrub_text(title)], timeout=10)
        except Exception:  # a missing notification never fails the round
            self.receipt.lines.append("桌面通知没发出去")

    @staticmethod
    def _later(item: dict[str, Any]) -> dict[str, Any]:
        return {"key": item["key"], "at": _iso(item["at_ts"]), "about": item["about"]}

    # ── events ──

    def opened(self, incident_id: int) -> None:
        inc = get_incident(self.conn, incident_id)
        if inc is None or self._done_before(incident_id, "opened"):
            return
        resource = get_resource(self.conn, inc["resource_id"])
        if inc.get("closed_at") is not None:
            # Opened and closed before this round ran: the close notice tells the whole story.
            self._mark(incident_id, "opened", card="skipped-closed")
            return
        outcomes = actions.on_open(self.heal, inc, resource)
        if not self.config.notifies(inc["severity"], inc["rule"]):
            self._mark(incident_id, "opened", card="quiet")
            self.receipt.lines.append(f"#{incident_id} 不发卡片（{inc['severity']}）")
            return
        hush = silence.silenced(self.vault, inc["rule"], inc["resource_id"], now=self.now)
        if hush:
            self._mark(incident_id, "opened", card="silenced", until=hush["until"])
            self.receipt.lines.append(f"#{incident_id} 静默到 {_hm(hush['until'])}")
            return
        title = f"{card_tag(incident_id)} 出事：{inc['summary'] or inc['rule']}"
        deferred = [o for o in outcomes if o.defer_card and o.result == DONE]
        if deferred:
            self._notice(f"{card_tag(incident_id)} 正在自愈：{inc['summary'] or inc['rule']}",
                         self._base_text(inc, resource) + [o.text for o in outcomes],
                         f"plane-incident-{incident_id}")
            for o in deferred:
                if o.later:
                    self.receipt.later.append(self._later(o.later))
            self._mark(incident_id, "opened", card="notice")
        else:
            self._ask(inc, resource, outcomes, title=title, key=f"plane-incident-{incident_id}")
            self._mark(incident_id, "opened", card="ask")
        self.receipt.lines.append(f"#{incident_id} 出事：{inc['resource_id']}")

    def closed(self, incident_id: int, open_cards: list[dict[str, Any]]) -> None:
        inc = get_incident(self.conn, incident_id)
        if inc is None or inc.get("closed_at") is None or self._done_before(incident_id, "closed"):
            return
        outcomes = actions.on_close(self.heal, inc)
        card = (self._slot(incident_id).get("opened") or {}).get("card")
        length = int(inc["closed_at"]) - int(inc["opened_at"])
        if card in NOTIFIED_CARDS:
            reason = ((inc.get("evidence") or {}).get("closed") or {}).get("reason")
            lines = [f"{inc['resource_id']}：{inc['summary'] or inc['rule']}",
                     f"{_hm(inc['opened_at'])} → {_hm(inc['closed_at'])}，"
                     f"持续 {minutes(length)} 分钟"]
            if reason == "gone":
                lines.append("资源已消失，事故随之关闭")
            lines += [o.text for o in outcomes]
            self._notice(f"{card_tag(incident_id)} 已恢复，持续 {minutes(length)} 分钟",
                         lines, f"plane-incident-{incident_id}-closed")
            tag = card_tag(incident_id)
            for c in open_cards:
                if tag in str(c.get("title", "")) and isinstance(c.get("card"), int):
                    self.receipt.resolve.append({"card": c["card"],
                                                 "reason": f"状态库里事故 #{incident_id} 已关闭"})
        if any(e.action == "kickstart" and e.result == DONE
               for e in audit.for_incident(self.vault, incident_id)):
            self.receipt.later.append({"key": actions.recheck_key(incident_id), "cancel": True})
        why = should_retro(self.conn, inc, self.config)
        if why:
            data = build_retro_input(self.conn, self.vault, inc, self.config, why=why,
                                     notes=self._notes(incident_id))
            file = write_retro_input(self.vault, incident_id, data)
            self.receipt.events.append(retro_event(inc, file, self.vault))
        self._mark(incident_id, "closed", retro=why)
        self.receipt.lines.append(f"#{incident_id} 恢复（{minutes(length)} 分钟）"
                                  + (f"，写复盘：{why}" if why else ""))

    def _notes(self, incident_id: int) -> list[dict[str, Any]]:
        slot = self._slot(incident_id)
        return [{"step": k, "at": _iso(v["at"]), "card": v.get("card"), "answer": v.get("answer")}
                for k, v in sorted(slot.items(), key=lambda kv: kv[1].get("at", 0))
                if isinstance(v, dict) and "at" in v]

    # ── answers and timers ──

    def answer(self, ans: dict[str, Any]) -> None:
        m = re.match(r"^incident:(\d+)", str(ans.get("about") or ""))
        if not m:
            return
        incident_id = int(m.group(1))
        options = ans.get("options") or []
        choice = ans.get("choice")
        picked = options[choice] if isinstance(choice, int) and 0 <= choice < len(options) else ""
        step = f"answer-{ans.get('card')}"
        if self._done_before(incident_id, step):
            return
        inc = get_incident(self.conn, incident_id)
        if inc is None:
            return
        self._mark(incident_id, step, answer=picked or (ans.get("text") or "")[:200])
        if picked == SILENCE:
            item = silence.add(self.vault, inc["rule"], inc["resource_id"], now=self.now,
                               days=self.config.silence_days, reason="卡片上选了忽略一周",
                               incident=incident_id)
            audit.record(self.vault, now=self.now, incident=incident_id, action="silence",
                         mode="owner", result=DONE, target=f"{inc['rule']} {inc['resource_id']}",
                         detail={"rule": inc["rule"], "resource": inc["resource_id"],
                                 "until": item["until"]},
                         undo="取消静默（kg plane heal undo <id>）")
            self.receipt.lines.append(f"#{incident_id} 静默到 {_hm(item['until'])}")
        elif picked == INVESTIGATE:
            data = build_retro_input(self.conn, self.vault, inc, self.config,
                                     why="本人交给 Agent 查", notes=self._notes(incident_id))
            folder = incident_dir(self.vault, incident_id)
            folder.mkdir(parents=True, exist_ok=True)
            file = folder / "investigate-input.json"
            file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            self.receipt.events.append({
                "id": f"plane-investigate-{incident_id}-{ans.get('card')}",
                "type": INVESTIGATE_EVENT, "subject": f"incident:{incident_id}",
                "summary": f"事故 #{incident_id} 交给 Agent 查", "detail": str(file.relative_to(
                    self.vault))})
            self.receipt.lines.append(f"#{incident_id} 交给 Agent 查")
        elif picked.startswith(SUGGEST_PREFIX):
            resource = get_resource(self.conn, inc["resource_id"])
            outs = actions.run_suggestion(self.heal, inc, resource)
            for o in outs:
                if o.later:
                    self.receipt.later.append(self._later(o.later))
            self._notice(f"{card_tag(incident_id)} 照建议做了", [o.text for o in outs],
                         f"plane-incident-{incident_id}-{step}")
            self.receipt.lines.append(f"#{incident_id} 照建议做：{'；'.join(o.text for o in outs)}")
        else:
            self.receipt.lines.append(f"#{incident_id} 本人：{picked or '写了说明'}")

    def timer(self, item: dict[str, Any]) -> None:
        m = re.match(r"^plane-recheck-(\d+)$", str(item.get("key") or ""))
        if not m:
            return
        incident_id = int(m.group(1))
        inc = get_incident(self.conn, incident_id)
        if inc is None or self._done_before(incident_id, "recheck"):
            return
        self._mark(incident_id, "recheck", still_open=inc.get("closed_at") is None)
        if inc.get("closed_at") is not None:
            return
        resource = get_resource(self.conn, inc["resource_id"])
        done = [e for e in audit.for_incident(self.vault, incident_id)
                if e.action == "kickstart" and e.result == DONE]
        note = actions.Outcome(actions.RESTART, "auto", "escalated",
                               f"{_hm(done[0].at) if done else '之前'} 自动重启过，"
                               f"{self.config.recheck_minutes} 分钟后仍没恢复，交给你")
        self._ask(inc, resource, [note],
                  title=f"{card_tag(incident_id)} 自动重启后仍挂：{inc['summary'] or inc['rule']}",
                  key=f"plane-incident-{incident_id}-escalate")
        self.receipt.lines.append(f"#{incident_id} 重启后仍挂，升级为人工")

    # ── one round ──

    def handle(self, context: dict[str, Any]) -> Receipt:
        found: list[tuple[int, int]] = []
        for event in context.get("events") or []:
            m = _EVENT_ID.match(str(event.get("id") or ""))
            if m:
                found.append((int(m.group(1)), 0 if m.group(2) == "opened" else 1))
        open_cards = list(context.get("open") or [])
        for incident_id, verb in sorted(set(found)):
            if verb == 0:
                self.opened(incident_id)
            else:
                self.closed(incident_id, open_cards)
        for ans in context.get("answers") or []:
            self.answer(ans)
        for item in context.get("timers") or []:
            self.timer(item)
        _save(self.vault, self.state)
        return self.receipt


def read_context(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}
