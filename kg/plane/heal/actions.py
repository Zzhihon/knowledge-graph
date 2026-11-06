"""What to do about an incident: one function per incident type, each `off` / `suggest` / `auto`.

    egress.outage  open  → pause every hub rule that `requires` egress (with an `until` cap)
                   close → resume them, then "run now" each rule the pause made skip something
    service.down   open  → `launchctl kickstart -k gui/<uid>/<label>`, at most N per hour,
                           and a re-check timer; still down at the re-check → a card for a human
    index.*        open  → mark the search index stale; close → mark it fresh

`suggest` writes what would help on the card (with a one-click option that runs it); `auto`
runs it. Everything ran or suggested goes to the audit log with how to undo it, and every
step checks the log first, so a round that runs twice (the hub is at-least-once) does not
pause, restart or run anything twice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kg.plane.heal import audit, silence
from kg.plane.heal.audit import DONE, FAILED, SKIPPED, SUGGEST_ONLY, SUGGESTED
from kg.plane.heal.hub_actions import (
    HubActionError,
    HubActions,
    HubUnsupportedError,
    NothingRequiresError,
)
from kg.plane.heal.index_status import mark_index_fresh, mark_index_stale

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from kg.plane.heal.config import HealConfig

EGRESS, RESTART, INDEX = "egress_pause", "service_restart", "index_stale"


@dataclass
class HealContext:
    vault: Path
    config: HealConfig
    now: int
    hub: HubActions | None = None
    run: Callable[..., str] | None = None  # run(argv, timeout=...) -> stdout, for launchctl
    uid: int = field(default_factory=os.getuid)


@dataclass
class Outcome:
    action: str
    mode: str
    result: str
    text: str  # one line for the card
    audit_id: str | None = None
    option: str | None = None  # card option that runs the suggestion
    later: dict[str, Any] | None = None  # a receipt `later` item (re-check timer)
    defer_card: bool = False  # the action is being tried: notice now, a card only if it fails


def kind_of(config: HealConfig, incident: dict[str, Any], resource: dict[str, Any] | None
            ) -> str | None:
    rule = incident["rule"]
    if rule in config.egress_rules:
        return EGRESS
    if rule.startswith(config.index_rule_prefix):
        return INDEX
    if rule in config.restart_rules and restart_label(config, resource) is not None:
        return RESTART
    return None


def restart_label(config: HealConfig, resource: dict[str, Any] | None) -> str | None:
    """The launchd label to kickstart for this service resource, if it is one of ours to restart."""
    if not resource or resource.get("kind") != "service":
        return None
    attrs = resource.get("attrs") or {}
    if attrs.get("group") not in config.restart_groups:
        return None
    if attrs.get("manager") == "launchd":
        if attrs.get("mode") not in config.restart_modes:
            return None
        return attrs.get("label") or None
    owner = attrs.get("owner")  # a port: restart the job that owns it
    return str(owner) if owner else None


def _record(ctx: HealContext, incident: dict[str, Any], action: str, mode: str, result: str,
            target: str, detail: dict[str, Any] | None = None, undo: str = "",
            undo_of: str | None = None) -> audit.AuditEntry:
    return audit.record(ctx.vault, now=ctx.now, incident=incident["id"], action=action,
                        mode=mode, result=result, target=target, detail=detail, undo=undo,
                        undo_of=undo_of)


def _suggested_before(ctx: HealContext, incident: dict[str, Any], action: str) -> bool:
    return any(e.action == action and e.result == SUGGESTED
               for e in audit.for_incident(ctx.vault, incident["id"]))


# ── egress: pause rules that need the egress line ─────────────────────────────────────────────


def _rule_names(ctx: HealContext) -> tuple[list[str] | None, str]:
    """Names of the rules requiring the capability, or None and why not."""
    if ctx.hub is None:
        return None, "没有配置中台"
    try:
        return [r.name for r in ctx.hub.list_rules_requiring(ctx.config.egress_capability)], ""
    except HubActionError as exc:
        return None, exc.message


def _pause(ctx: HealContext, incident: dict[str, Any], mode: str) -> Outcome:
    cap = ctx.config.egress_capability
    done = audit.active(ctx.vault, incident["id"], "pause_capability")
    if done:
        rules = done[0].detail.get("rules") or []
        return Outcome(EGRESS, mode, DONE,
                       f"已暂停 {len(rules)} 条依赖出口的规则：{'、'.join(rules)}", done[0].id)
    if ctx.hub is None:
        e = _record(ctx, incident, "pause_capability", mode, SUGGEST_ONLY, cap,
                    {"why": "没有配置中台"})
        return Outcome(EGRESS, mode, SUGGEST_ONLY, "建议手动暂停依赖出口的规则（没有配置中台）",
                       e.id)
    until = ctx.now + int(ctx.config.max_pause_hours * 3600)
    try:
        answer = ctx.hub.pause_capability(cap, f"事故 #{incident['id']}：出口断线", until)
    except HubUnsupportedError as exc:
        e = _record(ctx, incident, "pause_capability", mode, SUGGEST_ONLY, cap,
                    {"why": exc.message})
        return Outcome(EGRESS, mode, SUGGEST_ONLY,
                       "中台不支持按能力暂停，只能建议：手动运行 "
                       f"agent-autoloop pause --requires {cap}", e.id)
    except NothingRequiresError:
        e = _record(ctx, incident, "pause_capability", mode, SKIPPED, cap,
                    {"why": f"没有规则写了 requires {cap}"})
        return Outcome(EGRESS, mode, SKIPPED, f"没有规则写 requires {cap}，不用暂停", e.id)
    except HubActionError as exc:
        e = _record(ctx, incident, "pause_capability", mode, FAILED, cap, {"why": exc.message})
        return Outcome(EGRESS, mode, FAILED, f"暂停依赖出口的规则没成功：{exc.message}", e.id)
    rules = list(answer.get("rules") or [])
    e = _record(ctx, incident, "pause_capability", mode, DONE, cap,
                {"rules": rules, "until": until},
                undo=f"恢复 requires {cap} 的规则（kg plane heal undo <id>）")
    return Outcome(EGRESS, mode, DONE,
                   f"已暂停 {len(rules)} 条依赖出口的规则：{'、'.join(rules) or '无'}"
                   f"（最多停到 {ctx.config.max_pause_hours:g} 小时后）", e.id)


def _egress_open(ctx: HealContext, incident: dict[str, Any]) -> Outcome | None:
    mode = ctx.config.mode(EGRESS)
    if mode == "off":
        return None
    if mode == "auto":
        return _pause(ctx, incident, "auto")
    names, why = _rule_names(ctx)
    if names is not None and not names:
        return None
    which = f" {len(names)} 条（{'、'.join(names)}）" if names else ""
    text = f"建议：暂停依赖出口的规则{which}，恢复后补跑" + (f"（{why}）" if why else "")
    if not _suggested_before(ctx, incident, "pause_capability"):
        _record(ctx, incident, "pause_capability", "suggest", SUGGESTED,
                ctx.config.egress_capability, {"rules": names or []})
    return Outcome(EGRESS, "suggest", SUGGESTED, text, option="照建议做：暂停依赖出口的规则")


def _resume(ctx: HealContext, incident: dict[str, Any], pause: audit.AuditEntry, mode: str,
            why: str) -> list[Outcome]:
    cap = pause.target
    hub = ctx.hub
    if hub is None:
        return [Outcome(EGRESS, mode, FAILED, "没有配置中台，没法恢复规则")]
    try:
        answer = hub.resume_capability(cap)
    except HubActionError as exc:
        _record(ctx, incident, "resume_capability", mode, FAILED, cap, {"why": exc.message})
        return [Outcome(EGRESS, mode, FAILED, f"恢复依赖出口的规则没成功：{exc.message}")]
    still = list(answer.get("stillPaused") or [])
    e = _record(ctx, incident, "resume_capability", mode, DONE, cap,
                {"why": why, "rules": answer.get("rules") or [], "stillPaused": still},
                undo_of=pause.id)
    out = [Outcome(EGRESS, mode, DONE,
                   f"已恢复 requires {cap} 的规则" + (f"（仍单独暂停：{'、'.join(still)}）"
                                                     if still else ""), e.id)]
    out += _catch_up(ctx, incident, pause, mode)
    return out


def _catch_up(ctx: HealContext, incident: dict[str, Any], pause: audit.AuditEntry,
              mode: str) -> list[Outcome]:
    """Run once each rule the pause made skip a cron tick or an event (the hub never catches up)."""
    assert ctx.hub is not None
    already = {e.target for e in audit.for_incident(ctx.vault, incident["id"])
               if e.action == "run_rule" and e.result == DONE}
    try:
        refs = ctx.hub.list_rules_requiring(pause.target)
    except HubActionError as exc:
        return [Outcome(EGRESS, mode, FAILED, f"读不到错过了哪些规则，没有补跑：{exc.message}")]
    ran, out = [], []
    for ref in refs:
        if ref.skipped_at is None or ref.skipped_at < pause.at or ref.name in already:
            continue
        if ref.paused:
            _record(ctx, incident, "run_rule", mode, SKIPPED, ref.name, {"why": "还在暂停"})
            continue
        try:
            answer = ctx.hub.run_rule(ref.name, f"事故 #{incident['id']} 恢复后补跑")
        except HubActionError as exc:
            _record(ctx, incident, "run_rule", mode, FAILED, ref.name, {"why": exc.message})
            out.append(Outcome(EGRESS, mode, FAILED, f"补跑 {ref.name} 没成功：{exc.message}"))
            continue
        _record(ctx, incident, "run_rule", mode, DONE, ref.name,
                {"skipped_at": ref.skipped_at, "run": answer.get("run")})
        ran.append(ref.name)
    if ran:
        out.append(Outcome(EGRESS, mode, DONE, f"补跑了 {len(ran)} 条：{'、'.join(ran)}"))
    return out


# ── service: kickstart once, then a human ────────────────────────────────────────────────────


def _kickstart(ctx: HealContext, incident: dict[str, Any], label: str, mode: str) -> Outcome:
    entries = audit.read(ctx.vault)
    if any(e.incident == incident["id"] and e.action == "kickstart" and e.result == DONE
           for e in entries):
        return Outcome(RESTART, mode, DONE, f"已经重启过 {label}，等复查", defer_card=True)
    recent = [e for e in entries if e.action == "kickstart" and e.target == label
              and e.result == DONE and e.at > ctx.now - 3600]
    if len(recent) >= ctx.config.restart_per_hour:
        e = _record(ctx, incident, "kickstart", mode, SKIPPED, label,
                    {"why": f"1 小时内已重启 {len(recent)} 次"})
        return Outcome(RESTART, mode, SKIPPED,
                       f"1 小时内已经自动重启 {label} {len(recent)} 次，不再重启，交给你", e.id)
    argv = ["launchctl", "kickstart", "-k", f"gui/{ctx.uid}/{label}"]
    if ctx.run is None:
        from kg.plane.collectors import run_command

        ctx.run = run_command
    try:
        ctx.run(argv, timeout=20)
    except Exception as exc:  # CommandError, or whatever a fake runner raises
        e = _record(ctx, incident, "kickstart", mode, FAILED, label, {"why": str(exc)[:300]})
        return Outcome(RESTART, mode, FAILED, f"重启 {label} 没成功：{str(exc)[:200]}", e.id)
    e = _record(ctx, incident, "kickstart", mode, DONE, label, {"argv": argv[:3]},
                undo="重启不能撤销")
    minutes = ctx.config.recheck_minutes
    return Outcome(RESTART, mode, DONE, f"已重启 {label}，{minutes} 分钟后复查", e.id,
                   later={"key": recheck_key(incident["id"]), "at_ts": ctx.now + minutes * 60,
                          "about": f"incident:{incident['id']} 重启后复查"},
                   defer_card=True)


def recheck_key(incident_id: int) -> str:
    return f"plane-recheck-{incident_id}"


def _restart_open(ctx: HealContext, incident: dict[str, Any], resource: dict[str, Any] | None
                  ) -> Outcome | None:
    mode = ctx.config.mode(RESTART)
    label = restart_label(ctx.config, resource)
    if mode == "off" or label is None:
        return None
    if mode == "auto":
        return _kickstart(ctx, incident, label, "auto")
    if not _suggested_before(ctx, incident, "kickstart"):
        _record(ctx, incident, "kickstart", "suggest", SUGGESTED, label)
    return Outcome(RESTART, "suggest", SUGGESTED,
                   f"建议：launchctl kickstart -k gui/<uid>/{label}",
                   option=f"照建议做：重启 {label}")


# ── index ─────────────────────────────────────────────────────────────────────────────────────


def _index_open(ctx: HealContext, incident: dict[str, Any]) -> Outcome | None:
    mode = ctx.config.mode(INDEX)
    if mode == "off":
        return None
    if mode == "suggest":
        if not _suggested_before(ctx, incident, "index_stale"):
            _record(ctx, incident, "index_stale", "suggest", SUGGESTED, "index:kg:main")
        return Outcome(INDEX, "suggest", SUGGESTED, "建议：把索引标成过期，检索提示结果可能不全",
                       option="照建议做：标记索引过期")
    return _mark_stale(ctx, incident, mode)


def _mark_stale(ctx: HealContext, incident: dict[str, Any], mode: str) -> Outcome:
    done = audit.active(ctx.vault, incident["id"], "index_stale")
    if not done:
        mark_index_stale(ctx.vault, incident.get("summary") or incident["rule"],
                         incident=incident["id"], now=ctx.now)
        done = [_record(ctx, incident, "index_stale", mode, DONE, "index:kg:main",
                        undo="把索引标回正常（kg plane heal undo <id>）")]
    return Outcome(INDEX, mode, DONE, "已把索引标成过期，检索会提示结果可能不全", done[0].id)


# ── entry points ──────────────────────────────────────────────────────────────────────────────


def on_open(ctx: HealContext, incident: dict[str, Any], resource: dict[str, Any] | None
            ) -> list[Outcome]:
    kind = kind_of(ctx.config, incident, resource)
    outcome = None
    if kind == EGRESS:
        outcome = _egress_open(ctx, incident)
    elif kind == RESTART:
        outcome = _restart_open(ctx, incident, resource)
    elif kind == INDEX:
        outcome = _index_open(ctx, incident)
    return [outcome] if outcome else []


def run_suggestion(ctx: HealContext, incident: dict[str, Any],
                   resource: dict[str, Any] | None) -> list[Outcome]:
    """The owner picked "照建议做" on the card: run the suggested action now."""
    if incident.get("closed_at") is not None:
        return [Outcome("", "owner", SKIPPED, "事故已经恢复，不用再做")]
    kind = kind_of(ctx.config, incident, resource)
    if kind == EGRESS:
        return [_pause(ctx, incident, "owner")]
    if kind == RESTART:
        label = restart_label(ctx.config, resource)
        assert label is not None
        return [_kickstart(ctx, incident, label, "owner")]
    if kind == INDEX:
        return [_mark_stale(ctx, incident, "owner")]
    return []


def on_close(ctx: HealContext, incident: dict[str, Any]) -> list[Outcome]:
    """Undo what was done for the incident because it is over (resume, mark fresh)."""
    out: list[Outcome] = []
    for pause in audit.active(ctx.vault, incident["id"], "pause_capability"):
        out += _resume(ctx, incident, pause, pause.mode, "事故恢复")
    for stale in audit.active(ctx.vault, incident["id"], "index_stale"):
        mark_index_fresh(ctx.vault, now=ctx.now)
        e = _record(ctx, incident, "index_fresh", stale.mode, DONE, stale.target,
                    undo_of=stale.id)
        out.append(Outcome(INDEX, stale.mode, DONE, "索引标回正常", e.id))
    return out


def undo(ctx: HealContext, audit_id: str) -> Outcome:
    """Undo one audited action. Raises ValueError when it cannot be undone."""
    entries = audit.read(ctx.vault)
    entry = next((e for e in entries if e.id == audit_id), None)
    if entry is None:
        raise ValueError(f"审计记录里没有 {audit_id}")
    if entry.result != DONE or entry.undo_of:
        raise ValueError(f"{audit_id} 没有实际执行（{entry.result}），不用撤销")
    if entry.id in audit.undone_ids(entries):
        raise ValueError(f"{audit_id} 已经撤销过了")
    incident = {"id": entry.incident, "rule": "", "summary": ""}
    if entry.action == "pause_capability":
        outs = _resume(ctx, incident, entry, "owner", "本人撤销")
        return outs[0]
    if entry.action == "index_stale":
        mark_index_fresh(ctx.vault, now=ctx.now)
        e = _record(ctx, incident, "index_fresh", "owner", DONE, entry.target, undo_of=entry.id)
        return Outcome(INDEX, "owner", DONE, "索引标回正常", e.id)
    if entry.action == "silence":
        rule, resource = entry.detail.get("rule", ""), entry.detail.get("resource", "")
        silence.remove(ctx.vault, rule, resource, now=ctx.now)
        e = _record(ctx, incident, "unsilence", "owner", DONE, entry.target, undo_of=entry.id)
        return Outcome("silence", "owner", DONE, f"取消静默 {rule} {resource}", e.id)
    raise ValueError(f"{entry.action} 不能撤销（{entry.undo or '已经发生的事收不回来'}）")
