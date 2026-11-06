"""Alerting and self-healing (#9): cards, silence, heal actions, audit and undo, retro, report.

Everything runs on a temporary vault and state store. The hub is `FakeHubActions` or, for the
HTTP client and the end-to-end drill, a stub HTTP server on a free 127.0.0.1 port.
"""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import click
import pytest
from click.testing import CliRunner

from kg.plane import store
from kg.plane.heal import audit, silence
from kg.plane.heal.actions import HealContext, kind_of, on_close, on_open, restart_label, undo
from kg.plane.heal.config import HealConfig, parse_config
from kg.plane.heal.drill import OWNER_KEY, StubHub, run_drill
from kg.plane.heal.hub_actions import (
    FakeHubActions,
    HttpHubActions,
    HubActionError,
    HubUnsupportedError,
    NothingRequiresError,
)
from kg.plane.heal.index_status import read_index_status, search_warning
from kg.plane.heal.notify import Notifier
from kg.plane.heal.report import brief_block, to_markdown, weekly_report
from kg.plane.heal.retro import (
    build_retro_input,
    get_incident,
    link_entry,
    should_retro,
    validate_retro_entry,
)
from kg.plane.store import Resource, Signal

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

T0 = 1_800_000_000
EGRESS = "egress:main"
WEB = "service:launchd:com.example.web"


# ── helpers ──


def _vault(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    vault = tmp_path / "vault"
    (vault / ".kg" / "plane").mkdir(parents=True)
    conn = store.connect(store.db_path(vault))
    store.upsert_resources(conn, [
        Resource(EGRESS, "egress", "main", "egress"),
        Resource(WEB, "service", "web", "services",
                 {"manager": "launchd", "label": "com.example.web", "group": "mine",
                  "mode": "keepalive"}),
        Resource("service:launchd:com.example.tool", "service", "tool", "services",
                 {"manager": "launchd", "label": "com.example.tool", "group": "tool",
                  "mode": "keepalive"}),
        Resource("project:demo", "project", "demo", "sessions"),
    ], now=T0)
    return vault, conn


def _open(conn: sqlite3.Connection, rid: str, rule: str, at: int, severity: str = "warning",
          evidence: dict[str, Any] | None = None) -> int:
    iid = store.open_incident(conn, resource_id=rid, rule=rule, opened_at=at, severity=severity,
                              summary=f"{rule} 出事", evidence=evidence)
    assert iid is not None
    return iid


def _close(conn: sqlite3.Connection, rid: str, rule: str, at: int) -> None:
    store.close_incident(conn, resource_id=rid, rule=rule, closed_at=at, evidence={"ts": at})


def _ev(iid: int, verb: str, rid: str = EGRESS) -> dict[str, Any]:
    return {"id": f"plane-incident-{iid}-{verb}", "type": f"plane.incident.{verb}",
            "subject": rid}


def _config(**actions: str) -> HealConfig:
    return parse_config({"actions": actions})


def _hub() -> FakeHubActions:
    return FakeHubActions(rules={"夜间沉淀": ["egress"], "日报": ["egress"], "采集": []},
                          scheduled={"夜间沉淀"})


def _round(conn: sqlite3.Connection, vault: Path, ctx: dict[str, Any], *, now: int, run: str,
           config: HealConfig | None = None, hub: Any = None, runner: Any = None
           ) -> dict[str, Any]:
    n = Notifier(conn, vault, config or HealConfig(), now, run=run, hub=hub or _hub(),
                 runner=runner)
    return n.handle(ctx).body()


# ── notify: once per incident, recovery with duration, silence ──


def test_same_incident_is_announced_once_and_a_retry_of_the_round_repeats_it(tmp_path):
    vault, conn = _vault(tmp_path)
    iid = _open(conn, EGRESS, "egress.outage", T0)
    first = _round(conn, vault, {"events": [_ev(iid, "opened")]}, now=T0, run="7")
    assert len(first["ask"]) == 1
    card = first["ask"][0]
    assert card["key"] == f"plane-incident-{iid}" and card["about"] == f"incident:{iid}"
    assert card["options"][:3] == ["已处理", "忽略一周", "交给 Agent 查"]
    # Another round (redelivery, or the event merged into a later round): no second card.
    assert _round(conn, vault, {"events": [_ev(iid, "opened")]}, now=T0 + 60, run="8") == {}
    # The hub re-dispatching round 7 after a lost result: same card again, so it is not lost.
    retry = _round(conn, vault, {"events": [_ev(iid, "opened")]}, now=T0 + 90, run="7")
    assert retry["ask"][0]["title"] == card["title"]


def test_recovery_notice_carries_the_duration_and_resolves_the_open_card(tmp_path):
    vault, conn = _vault(tmp_path)
    iid = _open(conn, EGRESS, "egress.outage", T0)
    first = _round(conn, vault, {"events": [_ev(iid, "opened")]}, now=T0, run="1")
    _close(conn, EGRESS, "egress.outage", T0 + 25 * 60)
    body = _round(conn, vault, {"events": [_ev(iid, "closed")],
                                "open": [{"card": 9, "title": first["ask"][0]["title"]},
                                         {"card": 10, "title": "[事故 #99] 别的"}]},
                  now=T0 + 25 * 60, run="2")
    (notice,) = body["notice"]
    assert notice["title"] == f"[事故 #{iid}] 已恢复，持续 25 分钟"
    assert "持续 25 分钟" in notice["text"]
    assert body["resolve"] == [{"card": 9, "reason": f"状态库里事故 #{iid} 已关闭"}]
    # And only once.
    again = _round(conn, vault, {"events": [_ev(iid, "closed")]}, now=T0 + 26 * 60, run="3")
    assert "notice" not in again


def test_opened_and_closed_before_the_round_ran_gives_one_recovery_notice(tmp_path):
    vault, conn = _vault(tmp_path)
    iid = _open(conn, EGRESS, "egress.outage", T0)
    _close(conn, EGRESS, "egress.outage", T0 + 180)
    body = _round(conn, vault, {"events": [_ev(iid, "closed"), _ev(iid, "opened")]},
                  now=T0 + 200, run="1")
    assert "ask" not in body
    assert body["notice"][0]["title"].endswith("持续 3 分钟")


def test_info_and_muted_incidents_get_no_card(tmp_path):
    vault, conn = _vault(tmp_path)
    iid = _open(conn, "project:demo", "project.stalled", T0, severity="info")
    assert _round(conn, vault, {"events": [_ev(iid, "opened", "project:demo")]},
                  now=T0, run="1") == {}
    muted = parse_config({"notify": {"mute_rules": ["egress.outage"]}})
    iid2 = _open(conn, EGRESS, "egress.outage", T0)
    assert "ask" not in _round(conn, vault, {"events": [_ev(iid2, "opened")]}, now=T0,
                               run="2", config=muted)


def test_ignore_for_a_week_silences_the_kind_until_it_expires(tmp_path):
    vault, conn = _vault(tmp_path)
    iid = _open(conn, EGRESS, "egress.outage", T0)
    card = _round(conn, vault, {"events": [_ev(iid, "opened")]}, now=T0, run="1")["ask"][0]
    _round(conn, vault, {"answers": [{"card": 5, "title": card["title"],
                                      "options": card["options"], "choice": 1,
                                      "about": card["about"]}]}, now=T0 + 60, run="2")
    hush = silence.silenced(vault, "egress.outage", EGRESS, now=T0 + 120)
    assert hush is not None and hush["until"] == T0 + 60 + 7 * 86400
    (entry,) = [e for e in audit.read(vault) if e.action == "silence"]
    assert entry.undo.endswith(f"kg plane heal undo {entry.id}）")
    # The next outage of the same line within the week: opened and healed, but no card...
    _close(conn, EGRESS, "egress.outage", T0 + 600)
    iid2 = _open(conn, EGRESS, "egress.outage", T0 + 86400)
    body = _round(conn, vault, {"events": [_ev(iid2, "opened")]}, now=T0 + 86400, run="3")
    assert "ask" not in body
    _close(conn, EGRESS, "egress.outage", T0 + 86400 + 60)
    assert "notice" not in _round(conn, vault, {"events": [_ev(iid2, "closed")]},
                                  now=T0 + 86400 + 60, run="4")
    # ...and after it expires the card comes back.
    later = T0 + 8 * 86400
    iid3 = _open(conn, EGRESS, "egress.outage", later)
    assert len(_round(conn, vault, {"events": [_ev(iid3, "opened")]}, now=later,
                      run="5")["ask"]) == 1
    assert silence.active(vault, now=later) == {}


def test_undo_silence(tmp_path):
    vault, _ = _vault(tmp_path)
    silence.add(vault, "egress.outage", EGRESS, now=T0, days=7)
    entry = audit.record(vault, now=T0, incident=1, action="silence", mode="owner",
                         result="done", target="x",
                         detail={"rule": "egress.outage", "resource": EGRESS})
    undo(HealContext(vault, HealConfig(), T0 + 5), entry.id)
    assert silence.silenced(vault, "egress.outage", EGRESS, now=T0 + 10) is None


def test_hand_to_agent_emits_an_investigate_event_with_scrubbed_input(tmp_path):
    vault, conn = _vault(tmp_path)
    iid = _open(conn, EGRESS, "egress.outage", T0,
                evidence={"probes": [{"exit_ip": "198.51.100.23", "note": "token=abcd1234efgh"}]})
    card = _round(conn, vault, {"events": [_ev(iid, "opened")]}, now=T0, run="1")["ask"][0]
    body = _round(conn, vault, {"answers": [{"card": 3, "options": card["options"],
                                             "choice": 2, "about": card["about"]}]},
                  now=T0 + 30, run="2")
    (event,) = body["events"]
    assert event["type"] == "plane.incident.investigate" and event["subject"] == f"incident:{iid}"
    text = (vault / event["detail"]).read_text(encoding="utf-8")
    assert "198.51.100.23" not in text and "abcd1234efgh" not in text


# ── heal actions ──


def test_egress_default_is_suggest_and_the_card_option_runs_it(tmp_path):
    vault, conn = _vault(tmp_path)
    hub = _hub()
    iid = _open(conn, EGRESS, "egress.outage", T0)
    card = _round(conn, vault, {"events": [_ev(iid, "opened")]}, now=T0, run="1",
                  hub=hub)["ask"][0]
    assert ("pause", "egress") not in hub.calls
    assert "建议：暂停依赖出口的规则 2 条（夜间沉淀、日报）" in card["text"]
    assert card["options"][3] == "照建议做：暂停依赖出口的规则"
    assert card["recommend"] == 3
    body = _round(conn, vault, {"answers": [{"card": 4, "options": card["options"],
                                             "choice": 3, "about": card["about"]}]},
                  now=T0 + 60, run="2", hub=hub)
    assert hub.paused == {"egress"}
    assert "已暂停 2 条" in body["notice"][0]["text"]
    # Closing resumes what the owner paused, and catches up on the cron rule that missed a tick.
    hub.skipped_at["夜间沉淀"] = T0 + 300
    _close(conn, EGRESS, "egress.outage", T0 + 900)
    body = _round(conn, vault, {"events": [_ev(iid, "closed")]}, now=T0 + 900, run="3", hub=hub)
    assert hub.paused == set()
    assert ("run", "夜间沉淀") in hub.calls and ("run", "日报") not in hub.calls
    assert "补跑了 1 条：夜间沉淀" in body["notice"][0]["text"]


def test_egress_auto_pauses_once_and_resumes_on_close(tmp_path):
    vault, conn = _vault(tmp_path)
    hub = _hub()
    ctx = HealContext(vault, _config(egress_pause="auto"), T0, hub)
    inc = get_incident(conn, _open(conn, EGRESS, "egress.outage", T0))
    (out,) = on_open(ctx, inc, None)
    assert out.result == "done" and hub.calls.count(("pause", "egress")) == 1
    on_open(ctx, inc, None)  # the same round again: no second pause
    assert hub.calls.count(("pause", "egress")) == 1
    _close(conn, EGRESS, "egress.outage", T0 + 600)
    outs = on_close(HealContext(vault, ctx.config, T0 + 600, hub), get_incident(conn, inc["id"]))
    assert outs[0].text.startswith("已恢复") and hub.paused == set()
    assert on_close(HealContext(vault, ctx.config, T0 + 660, hub), inc) == []


def test_old_hub_falls_back_to_suggesting(tmp_path):
    vault, conn = _vault(tmp_path)
    hub = FakeHubActions(rules={"夜间沉淀": ["egress"]}, unsupported=True)
    iid = _open(conn, EGRESS, "egress.outage", T0)
    card = _round(conn, vault, {"events": [_ev(iid, "opened")]}, now=T0, run="1", hub=hub,
                  config=_config(egress_pause="auto"))["ask"][0]
    assert "中台不支持按能力暂停，只能建议" in card["text"]
    (entry,) = audit.read(vault)
    assert entry.result == "suggest-only" and "版本旧" in entry.detail["why"]


def test_off_means_nothing(tmp_path):
    vault, conn = _vault(tmp_path)
    hub = _hub()
    inc = get_incident(conn, _open(conn, EGRESS, "egress.outage", T0))
    assert on_open(HealContext(vault, _config(egress_pause="off"), T0, hub), inc, None) == []
    assert hub.calls == []


def test_restart_only_our_keepalive_services():
    config = HealConfig()
    mine = {"kind": "service", "attrs": {"manager": "launchd", "label": "a", "group": "mine",
                                         "mode": "keepalive"}}
    assert restart_label(config, mine) == "a"
    assert restart_label(config, {**mine, "attrs": {**mine["attrs"], "group": "tool"}}) is None
    assert restart_label(config, {**mine, "attrs": {**mine["attrs"], "mode": "scheduled"}}) is None
    port = {"kind": "service", "attrs": {"group": "mine", "owner": "b"}}
    assert restart_label(config, port) == "b"
    assert kind_of(config, {"rule": "service.down"}, port) == "service_restart"
    assert kind_of(config, {"rule": "service.exposed"}, port) is None


def test_service_auto_restart_rechecks_and_escalates(tmp_path):
    vault, conn = _vault(tmp_path)
    calls: list[list[str]] = []
    config = _config(service_restart="auto")

    def runner(argv, timeout=20):
        calls.append(list(argv))
        return ""

    iid = _open(conn, WEB, "service.down", T0, severity="critical")
    body = _round(conn, vault, {"events": [_ev(iid, "opened", WEB)]}, now=T0, run="1",
                  config=config, runner=runner)
    assert calls == [["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.example.web"]]
    assert "ask" not in body and body["notice"][0]["title"].startswith(f"[事故 #{iid}] 正在自愈")
    (later,) = body["later"]
    assert later["key"] == f"plane-recheck-{iid}"
    # Ten minutes later it is still down: a card for a human.
    body = _round(conn, vault, {"timers": [{"key": later["key"]}]}, now=T0 + 600, run="2",
                  config=config, runner=runner)
    (card,) = body["ask"]
    assert "自动重启后仍挂" in card["title"] and card["key"] == f"plane-incident-{iid}-escalate"
    assert len(calls) == 1


def test_service_restart_is_rate_limited(tmp_path):
    vault, conn = _vault(tmp_path)
    config = _config(service_restart="auto")
    calls = []
    for n in range(3):
        at = T0 + n * 900
        inc = get_incident(conn, _open(conn, WEB, "service.down", at))
        resource = store.get_resource(conn, WEB)
        (out,) = on_open(HealContext(vault, config, at, run=lambda a, timeout=20: calls.append(a)
                                     or ""), inc, resource)
        _close(conn, WEB, "service.down", at + 60)
    assert len(calls) == 2
    assert out.result == "skipped" and "1 小时内已经自动重启" in out.text


def test_index_incident_marks_the_index_stale_and_close_marks_it_fresh(tmp_path):
    vault, conn = _vault(tmp_path)
    store.upsert_resources(conn, [Resource("service:index:kg", "service", "index", "x")], now=T0)
    assert search_warning(vault) is None
    inc = get_incident(conn, _open(conn, "service:index:kg", "index.failed", T0))
    ctx = HealContext(vault, _config(index_stale="auto"), T0)
    (out,) = on_open(ctx, inc, None)
    assert read_index_status(vault).stale and "结果可能不全" in search_warning(vault)
    undo(ctx, out.audit_id)
    assert search_warning(vault) is None
    with pytest.raises(ValueError, match="已经撤销过"):
        undo(ctx, out.audit_id)


def test_undo_a_pause_resumes_and_a_restart_cannot_be_undone(tmp_path):
    vault, conn = _vault(tmp_path)
    hub = _hub()
    ctx = HealContext(vault, _config(egress_pause="auto"), T0, hub)
    inc = get_incident(conn, _open(conn, EGRESS, "egress.outage", T0))
    (out,) = on_open(ctx, inc, None)
    assert undo(ctx, out.audit_id).result == "done" and hub.paused == set()
    # The incident closing later does not resume twice.
    on_close(ctx, inc)
    assert hub.calls.count(("resume", "egress")) == 1
    entry = audit.record(vault, now=T0, incident=1, action="kickstart", mode="auto",
                         result="done", target="x", undo="重启不能撤销")
    with pytest.raises(ValueError, match="不能撤销"):
        undo(ctx, entry.id)


# ── the hub over HTTP ──


def test_http_hub_actions_against_a_stub(tmp_path):
    clock = [T0]
    stub = StubHub(clock)
    url = stub.start()
    try:
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"url": url, "owner": OWNER_KEY}))
        hub = HttpHubActions(cfg, timeout=5)
        assert {r.name for r in hub.list_rules_requiring("egress")} == {"夜间沉淀", "日报"}
        assert hub.pause_capability("egress", "测试", T0 + 60)["rules"] == ["夜间沉淀", "日报"]
        with pytest.raises(HubActionError) as exc:
            hub.run_rule("夜间沉淀")
        assert exc.value.status == 409
        with pytest.raises(NothingRequiresError):
            hub.pause_capability("gpu", "测试")
        stub.rules = {}  # the stub now has nothing: unknown paths look like an old hub
        with pytest.raises(HubUnsupportedError):
            hub._call("POST", "/v1/whatever/x/pause", {})
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"url": url, "owner": "wrong-key-123"}))
        with pytest.raises(HubActionError) as exc:
            HttpHubActions(bad, timeout=5).resume_capability("egress")
        assert exc.value.status == 401 and "wrong-key-123" not in exc.value.message
    finally:
        stub.stop()
    assert all(q["path"].startswith("/v1/") for q in stub.requests)


# ── retro ──


def test_retro_only_for_long_or_repeated_incidents(tmp_path):
    vault, conn = _vault(tmp_path)
    config = HealConfig()
    a = _open(conn, EGRESS, "egress.outage", T0)
    _close(conn, EGRESS, "egress.outage", T0 + 5 * 60)
    assert should_retro(conn, get_incident(conn, a), config) is None
    b = _open(conn, EGRESS, "egress.outage", T0 + 3600)
    _close(conn, EGRESS, "egress.outage", T0 + 3600 + 120)
    assert should_retro(conn, get_incident(conn, b), config) == "同类第 2 次"
    c = _open(conn, WEB, "service.down", T0)
    _close(conn, WEB, "service.down", T0 + 11 * 60)
    assert should_retro(conn, get_incident(conn, c), config) == "持续 11 分钟"
    link_entry(conn, c, "ke-20270115-service-down-incident-3")
    assert should_retro(conn, get_incident(conn, c), config) is None


def test_retro_event_on_close_and_previous_retro_on_the_next_card(tmp_path):
    vault, conn = _vault(tmp_path)
    store.append_signals(conn, [Signal(EGRESS, T0 + i * 60, "down" if 2 <= i < 14 else "up",
                                       detail={"reason": "TLS 握手失败",
                                               "exit_ip": "203.0.113.9"})
                                for i in range(16)])
    iid = _open(conn, EGRESS, "egress.outage", T0 + 180,
                evidence={"started_at": T0 + 120, "probes": [{"exit_ip": "203.0.113.9"}]})
    _round(conn, vault, {"events": [_ev(iid, "opened")]}, now=T0 + 180, run="1")
    _close(conn, EGRESS, "egress.outage", T0 + 14 * 60)
    body = _round(conn, vault, {"events": [_ev(iid, "closed")]}, now=T0 + 14 * 60, run="2")
    (event,) = body["events"]
    assert event == {"id": f"plane-retro-{iid}", "type": "incident.retro.ready",
                     "subject": f"incident:{iid}", "summary": f"事故 #{iid} 待写复盘：{EGRESS}",
                     "detail": f".kg/plane/incidents/{iid}/retro-input.json"}
    data = json.loads((vault / event["detail"]).read_text())
    assert [s["status"] for s in data["timeline"]] == ["up", "down", "up"]
    assert data["incident"]["duration_minutes"] == 11.0
    assert data["write"]["link_command"].startswith(f"kg plane incident link {iid} ke-")
    assert "203.0.113.9" not in json.dumps(data, ensure_ascii=False)
    # Claude wrote the entry and linked it; the next outage's card points at it.
    entry_id = data["write"]["entry_id"]
    (vault / "03-Debug").mkdir()
    (vault / "03-Debug" / f"{entry_id}.md").write_text(GOOD_ENTRY, encoding="utf-8")
    link_entry(conn, iid, entry_id)
    nxt = _open(conn, EGRESS, "egress.outage", T0 + 86400)
    card = _round(conn, vault, {"events": [_ev(nxt, "opened")]}, now=T0 + 86400,
                  run="3")["ask"][0]
    assert f"[[{entry_id}]]（03-Debug/{entry_id}.md）" in card["text"]


def test_retro_input_names_the_actions_taken(tmp_path):
    vault, conn = _vault(tmp_path)
    hub = _hub()
    inc = get_incident(conn, _open(conn, EGRESS, "egress.outage", T0))
    on_open(HealContext(vault, _config(egress_pause="auto"), T0, hub), inc, None)
    data = build_retro_input(conn, vault, inc, HealConfig())
    assert [a["action"] for a in data["actions"]] == ["pause_capability"]


GOOD_ENTRY = """---
id: ke-20270115-egress-outage-incident-1
title: 专用出口 TLS 握手失败 12 分钟
type: debug
domain: [network]
tags: [egress, incident]
status: draft
scope: personal
source:
  type: experience
  date: "2027-01-15"
  context: 管控面事故 #1 自动复盘
  incident: 1
related: []
confidence: 0.6
created: "2027-01-15"
updated: "2027-01-15"
---

结论：上游断线，代理进程正常。

```mermaid
flowchart LR
  A[探测连续失败] --> B[TLS 握手失败] --> C[上游节点不可用]:::root --> D[等上游恢复]:::fix
  classDef root stroke-width:2px
```

## 现象
探测连续 12 分钟失败。

## 排查过程
看分段：本地端口在，经代理的握手失败。

## 根因
上游节点不可用（未验证）。

## 修复
暂停依赖出口的规则，恢复后补跑。

## 怎么验证
恢复后探测连续成功。

## 以后怎么避免
备用节点。
"""


def test_retro_entry_check_passes_a_good_entry_and_names_what_is_missing():
    assert validate_retro_entry(GOOD_ENTRY) == []
    bad = (GOOD_ENTRY.replace("type: debug", "type: experience")
           .replace("  incident: 1\n", "").replace("```mermaid", "```text")
           .replace("## 根因", "## 原因").replace("C[上游节点不可用]", "C[203.0.113.9 不通]"))
    problems = validate_retro_entry(bad)
    assert "type 应该是 debug，不是 experience" in problems
    assert "缺 source.incident（事故编号）" in problems
    assert "没有 mermaid 因果图（```mermaid flowchart）" in problems
    assert "缺「根因」一节" in problems
    assert "正文里有 IP 地址，要脱敏" in problems
    assert validate_retro_entry("no frontmatter") == ["没有 frontmatter"]
    no_title = GOOD_ENTRY.replace("title: 专用出口 TLS 握手失败 12 分钟\n", "")
    assert "缺 title" in validate_retro_entry(no_title)


def test_link_entry_rejects_bad_ids(tmp_path):
    _, conn = _vault(tmp_path)
    iid = _open(conn, EGRESS, "egress.outage", T0)
    with pytest.raises(ValueError):
        link_entry(conn, iid, "../../etc/passwd")
    with pytest.raises(ValueError, match="没有事故"):
        link_entry(conn, 999, "ke-20270115-x-y")


# ── weekly report and the daily brief ──


def test_weekly_report_availability_and_error_budget(tmp_path):
    vault, conn = _vault(tmp_path)
    now = T0 + 7 * 86400
    # Egress: 100 readings, 10 down, 5 asleep (not counted).
    store.append_signals(conn, [Signal(EGRESS, T0 + i * 600,
                                       "down" if i < 10 else "asleep" if i < 15 else "up")
                                for i in range(100)])
    # Two overlapping warning incidents (30 + 30 minutes, 15 shared) and an info one.
    _open(conn, EGRESS, "egress.outage", T0)
    _close(conn, EGRESS, "egress.outage", T0 + 1800)
    _open(conn, EGRESS, "egress.identity", T0 + 900)
    _close(conn, EGRESS, "egress.identity", T0 + 2700)
    _open(conn, EGRESS, "egress.purity", T0, severity="info")
    _close(conn, EGRESS, "egress.purity", T0 + 7200)
    rows = {r.id: r for r in weekly_report(conn, HealConfig(), now=now)}
    egress = rows[EGRESS]
    assert egress.availability == pytest.approx(85 / 95)
    assert egress.incidents == 3 and egress.spent_minutes == 45
    assert egress.budget_minutes == pytest.approx(7 * 1440 * 0.005)
    assert egress.budget_left == pytest.approx(1 - 45 / (7 * 1440 * 0.005))
    assert WEB in rows and "service:launchd:com.example.tool" not in rows
    md = to_markdown(list(rows.values()), now=now)
    assert md.startswith("### 管控面周报") and "| main `egress:main` | 89.47% | 3 |" in md


def test_brief_gets_a_plane_block(tmp_path):
    from kg.capture.brief import collect_brief
    vault, conn = _vault(tmp_path)
    now = int(time.time())
    iid = _open(conn, EGRESS, "egress.outage", now - 3600)
    _close(conn, EGRESS, "egress.outage", now - 600)
    link_entry(conn, iid, "ke-20270115-egress-outage-incident-1")
    (vault / "03-Debug").mkdir()
    (vault / "03-Debug" / "ke-20270115-egress-outage-incident-1.md").write_text(GOOD_ENTRY)
    conn.close()
    block = brief_block(vault, HealConfig(), since=now - 86400, now=now)
    assert block["retros"] == [{"incident": iid, "entry_id": "ke-20270115-egress-outage-incident-1",
                                "path": "03-Debug/ke-20270115-egress-outage-incident-1.md"}]
    assert block["incidents"][0]["minutes"] == 50
    config = SimpleNamespace(brief={"aihot": False}, inbox=vault / "no-inbox")
    _, file = collect_brief(vault, config, fetch=False)
    data = json.loads(file.read_text())
    assert data["plane"]["retros"][0]["incident"] == iid
    assert "管控面周报" in data["plane"]["week"]


def test_brief_without_a_store_has_no_plane_block(tmp_path):
    assert brief_block(tmp_path, HealConfig(), since=0, now=T0) is None


# ── end to end: a simulated egress outage, nobody touching anything ──


def test_simulated_egress_outage_end_to_end(tmp_path):
    result = run_drill(tmp_path)
    assert result.problems == []
    paths = [(q["method"], q["path"]) for q in result.requests]
    assert paths.index(("POST", "/v1/requires/egress/pause")) \
        < paths.index(("POST", "/v1/requires/egress/resume")) \
        < paths.index(("POST", "/v1/rules/夜间沉淀/run"))
    (inc,) = result.incidents
    pause = next(q for q in result.requests if q["path"].endswith("/pause"))
    resume = next(q for q in result.requests if q["path"].endswith("/resume"))
    assert pause["at"] == inc["opened_at"] and resume["at"] == inc["closed_at"]
    (retro,) = result.retro_events
    data = json.loads(result.files[retro["detail"]])
    assert [a["action"] for a in data["actions"]] == ["pause_capability", "resume_capability",
                                                      "run_rule"]


# ── the CLI ──


def _cli() -> click.Group:
    from agents.cli_plane_heal import register

    @click.group()
    def plane() -> None:
        pass

    register(plane)
    return plane


def test_cli_notify_link_undo_and_report(tmp_path, monkeypatch):
    vault, conn = _vault(tmp_path)
    iid = _open(conn, EGRESS, "egress.outage", T0)
    conn.close()
    monkeypatch.setattr("kg.interfaces.cli.commands.plane_heal._vault", lambda console: vault)
    monkeypatch.setattr("kg.interfaces.cli.commands.plane_heal._hub", lambda v: _hub())
    ctx = tmp_path / "context.json"
    ctx.write_text(json.dumps({"events": [_ev(iid, "opened")]}))
    monkeypatch.setenv("AGENT_AUTOLOOP_CONTEXT", str(ctx))
    monkeypatch.setenv("AGENT_AUTOLOOP_RUN", "12")
    runner = CliRunner()
    out = runner.invoke(_cli(), ["notify", "--autoloop"])
    assert out.exit_code == 0, out.output
    head, block = out.output.split("```agent-autoloop\n")
    assert head.startswith(f"#{iid} 出事") and json.loads(block.split("```")[0])["ask"]
    # Already announced: the next round says there is nothing to do.
    monkeypatch.setenv("AGENT_AUTOLOOP_RUN", "13")
    assert runner.invoke(_cli(), ["notify", "--autoloop"]).output.startswith("没事可做")

    (vault / "03-Debug").mkdir()
    entry = "ke-20270115-egress-outage-incident-1"
    (vault / "03-Debug" / f"{entry}.md").write_text(GOOD_ENTRY.replace("## 修复", "## 别的"))
    bad = runner.invoke(_cli(), ["incident", "link", str(iid), entry])
    assert bad.exit_code == 1 and "缺「修复」一节" in bad.output
    (vault / "03-Debug" / f"{entry}.md").write_text(GOOD_ENTRY)
    assert runner.invoke(_cli(), ["incident", "link", str(iid), entry]).exit_code == 0
    conn = store.connect(store.db_path(vault))
    assert get_incident(conn, iid)["entry_id"] == entry

    entry_audit = audit.record(vault, now=T0, incident=iid, action="index_stale", mode="auto",
                               result="done", target="index:kg:main")
    assert runner.invoke(_cli(), ["heal", "undo", entry_audit.id]).exit_code == 0
    assert runner.invoke(_cli(), ["heal", "undo", entry_audit.id]).exit_code == 1
    listed = json.loads(runner.invoke(_cli(), ["heal", "list", "--json"]).output)
    assert next(i for i in listed if i["id"] == entry_audit.id)["undone"] is True
    report = runner.invoke(_cli(), ["report", "--week"])
    assert report.exit_code == 0 and "管控面周报" in report.output


# ── the 事故 page's API ──


def test_incidents_board_and_undo_need_same_origin_json(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agents.api_routes import incidents

    vault, conn = _vault(tmp_path)
    hub = _hub()
    store.append_signals(conn, [Signal(EGRESS, T0 + i * 60, "down" if i < 3 else "up")
                                for i in range(5)])
    inc = get_incident(conn, _open(conn, EGRESS, "egress.outage", T0))
    (out,) = on_open(HealContext(vault, _config(egress_pause="auto"), T0, hub), inc, None)
    app = FastAPI()
    app.include_router(incidents.router, prefix="/api")
    app.dependency_overrides[incidents.heal_vault] = lambda: vault
    app.dependency_overrides[incidents.heal_hub] = lambda: hub
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    board = client.get("/api/plane/incidents/board").json()
    (item,) = board["open"]
    assert item["id"] == inc["id"] and item["timeline"][0]["status"] == "down"
    assert item["actions"][0]["undoable"] is True
    # A write from another site, or not JSON, is refused.
    assert client.post("/api/plane/heal/undo", json={"id": out.audit_id},
                       headers={"origin": "http://evil.example"}).status_code == 403
    ok = client.post("/api/plane/heal/undo", json={"id": out.audit_id},
                     headers={"origin": "http://127.0.0.1:8000"})
    assert ok.status_code == 200 and hub.paused == set()
    again = client.post("/api/plane/heal/undo", json={"id": out.audit_id},
                        headers={"origin": "http://127.0.0.1:8000"})
    assert again.status_code == 409 and "已经撤销过" in again.json()["detail"]
    board = client.get("/api/plane/incidents/board").json()
    assert board["open"][0]["actions"][0]["undone"] is True


def test_marking_the_index_keeps_the_index_builders_own_fields(tmp_path):
    # kg.retrieval writes ok / last_success / error into the same status.json; the heal action
    # used to overwrite the whole file and wipe them.
    from kg.plane.heal.index_status import mark_index_fresh, mark_index_stale, status_path

    file = status_path(tmp_path)
    file.parent.mkdir(parents=True)
    built = {"ok": True, "last_success": "2026-11-06T05:30:00+08:00", "files": 3}
    file.write_text(json.dumps(built))
    mark_index_stale(tmp_path, "建索引失败", incident=7, now=100)
    raw = json.loads(file.read_text())
    assert raw["stale"] and raw["incident"] == 7
    assert (raw["ok"], raw["last_success"], raw["files"]) == (True, "2026-11-06T05:30:00+08:00", 3)
    mark_index_fresh(tmp_path, now=200)
    raw = json.loads(file.read_text())
    assert not raw["stale"] and raw["files"] == 3
