"""/api/overview: what needs you comes first, and one broken source only empties its own block.

The hub is a fake client and the state store a tmp file; the real hub and vault are never touched.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.api_routes import overview as ov
from agents.api_routes.hub import get_client
from agents.api_routes.plane import plane_db_path
from agents.autoloop import HubError
from kg.plane import store
from kg.plane.store import Resource, Signal

if TYPE_CHECKING:
    from pathlib import Path

ORIGIN = "http://127.0.0.1:8765"


def card(i: int, created: str, kind: str = "ask") -> dict[str, Any]:
    return {"id": i, "version": 1, "kind": kind, "state": "open", "title": f"卡片 {i}", "text": "",
            "options": ["好"], "recommend": 0, "rule": "整理会话", "createdAt": created}


def incident(i: int, severity: str, opened_at: int) -> dict[str, Any]:
    return {"id": i, "resource_id": f"service:port:{9000 + i}", "rule": "service.down",
            "opened_at": opened_at, "closed_at": None, "severity": severity, "summary": f"事故 {i}"}


# ── ranking ──


def test_critical_incident_beats_card_beats_warning_beats_session():
    items = ov.attention_items(
        [card(7, "2026-11-06T08:00:00+08:00")],
        [incident(1, "warning", 100), incident(2, "critical", 200), incident(3, "info", 50)],
        [{"id": "s1", "title": "等你回答", "since": 10}],
    )
    assert [(x["kind"], x["id"]) for x in items] == [
        ("incident", 2), ("card", 7), ("incident", 1), ("incident", 3), ("session", "s1")]


def test_same_urgency_oldest_first_and_closed_incidents_dropped():
    items = ov.attention_items(
        [card(2, "2026-11-06T10:00:00Z"), card(1, "2026-11-06T09:00:00Z", "notice")],
        [{**incident(5, "warning", 10), "closed_at": 20}],
    )
    assert [x["id"] for x in items] == [1, 2]
    assert items[0]["ask"] is False and items[1]["ask"] is True


# ── health ──


def test_group_status_words():
    assert ov.group_status(["up", "asleep"]) == "up"
    assert ov.group_status(["up", "degraded"]) == "down"
    assert ov.group_status(["asleep", "asleep"]) == "asleep"
    assert ov.group_status(["unknown", None]) == "unknown"
    assert ov.group_status([]) == "unknown"


def test_availability_ignores_asleep_and_weights_rollups():
    sig = [Signal("r", 1, "up"), Signal("r", 2, "down"), Signal("r", 3, "asleep"),
           Signal("r", 4, "up", samples=2)]
    assert ov.availability(sig) == 0.75
    assert ov.availability([Signal("r", 1, "asleep")]) is None


def test_period_is_median_gap_and_stale_after_two_missed_reports():
    assert ov.infer_period([0, 300, 600, 900, 2000]) == 300
    assert ov.infer_period([5]) == ov.DEFAULT_PERIOD
    assert ov.infer_period([0, 10, 20]) == ov.MIN_PERIOD
    assert not ov.is_stale(1000, 300, 1000 + 600)
    assert ov.is_stale(1000, 300, 1000 + 601)
    assert ov.is_stale(None, 300, 1000)


def test_pill_without_resources_is_planned_or_unknown():
    assert ov.health_pill("egress", "出口", [], {}, 0, planned=4)["text"] == "规划中 #4"
    assert ov.health_pill("x", "X", [], {}, 0)["status"] == "unknown"


# ── endpoint ──


class FakeHub:
    def __init__(self, state: dict[str, Any] | None = None, error: str | None = None):
        self.state, self.error = state, error

    def call(self, method: str, path: str, body: Any = None) -> Any:
        assert (method, path) == ("GET", "/v1/state")
        if self.error:
            raise HubError(502, self.error)
        return self.state


HUB_STATE = {
    "cards": [card(41, "2026-11-06T01:00:00Z")],
    "totals": {"doneToday": 6, "running": 1},
    "spending": {"today": {"cost": 1.25, "missingCost": 0}},
    "agents": [{"id": "claude", "name": "Claude Code", "online": True}],
}


@pytest.fixture
def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("KG_INDEX_DIR", raising=False)
    db = tmp_path / "state.db"
    vault = tmp_path / "vault"
    (vault / "00-Index" / "Daily").mkdir(parents=True)
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    (vault / "00-Index" / "Daily" / f"{today}.md").write_text(
        "# 日报\n\n> 一句话：今天收了三条经验。\n\n## 项目\n", encoding="utf-8")
    xp = vault / "10-Experience"
    xp.mkdir()
    (xp / "a.md").write_text(f'---\ntitle: a\ncreated: "{today}"\n---\n正文\n', encoding="utf-8")
    (xp / "b.md").write_text('---\ntitle: b\ncreated: "2020-01-01"\n---\n正文\n', encoding="utf-8")

    def build(hub: FakeHub) -> TestClient:
        app = FastAPI()
        app.include_router(ov.router, prefix="/api")
        app.dependency_overrides[get_client] = lambda: hub
        app.dependency_overrides[plane_db_path] = lambda: db
        app.dependency_overrides[ov.overview_vault] = lambda: vault
        return TestClient(app, base_url=ORIGIN)

    build.vault = vault  # type: ignore[attr-defined]
    return build, db


def open_incident(db: Path, severity: str = "warning") -> None:
    conn = store.connect(db)
    with store.transaction(conn):
        store.open_incident(conn, resource_id="service:port:9999", rule="service.down",
                            opened_at=int(time.time()), severity=severity, summary="端口不在监听")
    conn.close()


@pytest.mark.parametrize("severity", ["warning", "critical"])
def test_made_up_card_and_incident_are_the_first_two(make, severity):
    build, db = make
    open_incident(db, severity)
    data = build(FakeHub(HUB_STATE)).get("/api/overview").json()
    top = [(x["kind"], x["id"]) for x in data["attention"]["items"][:2]]
    assert set(top) == {("card", 41), ("incident", 1)}
    assert top[0][0] == ("incident" if severity == "critical" else "card")
    assert data["attention"]["ok"] and data["incidents"] == {"ok": True, "open": 1}


def test_hub_down_only_empties_hub_blocks(make):
    build, db = make
    open_incident(db)
    data = build(FakeHub(error="中台没响应：connection refused")).get("/api/overview").json()
    assert [x["kind"] for x in data["attention"]["items"]] == ["incident"]
    assert data["attention"]["errors"] == {"hub": "中台没响应：connection refused"}
    assert data["automation"]["ok"] is False and "中台" in data["automation"]["error"]
    hub = next(p for p in data["health"]["pills"] if p["id"] == "hub")
    assert hub["status"] == "down" and "connection refused" in hub["reason"]
    # everything else answers as usual
    assert data["daily"]["ok"] and data["daily"]["lead"] == "今天收了三条经验。"
    assert data["fresh"] == {"ok": True, "count": 1}
    assert data["incidents"]["open"] == 1 and data["health"]["ok"]


def test_broken_store_only_empties_store_blocks(make):
    build, db = make
    db.write_text("this is not sqlite")
    data = build(FakeHub(HUB_STATE)).get("/api/overview").json()
    assert data["incidents"]["ok"] is False and data["health"]["ok"] is False
    assert [x["kind"] for x in data["attention"]["items"]] == ["card"]
    assert "incidents" in data["attention"]["errors"]
    assert data["automation"] == {"ok": True, "rounds": 6, "running": 1, "cost": 1.25,
                                  "missingCost": 0}


def test_health_pills_from_store_with_staleness(make):
    build, db = make
    now = int(time.time())
    conn = store.connect(db)
    with store.transaction(conn):
        store.upsert_resources(conn, [Resource("egress:main", "egress", "main", "egress")], now=now)
        # reported every 5 minutes, last report 20 minutes ago: more than two periods missed
        store.append_signals(conn, [Signal("egress:main", now - 1200 - k * 300, st)
                                    for k, st in enumerate(["up", "up", "down", "up"])])
    conn.close()
    data = build(FakeHub(HUB_STATE)).get("/api/overview").json()
    pills = {p["id"]: p for p in data["health"]["pills"]}
    egress = pills["egress"]
    assert egress["status"] == "up" and egress["availability"] == 0.75
    assert egress["period"] == 300 and egress["stale"] is True
    assert pills["services"]["text"] == "未知"
    # no index files in the tmp vault: the pill says why instead of "规划中"
    assert pills["index"]["status"] == "down" and "还没建" in pills["index"]["reason"]
    assert pills["hub"]["status"] == "up"


def test_fresh_signal_is_not_stale(make):
    build, db = make
    now = int(time.time())
    conn = store.connect(db)
    with store.transaction(conn):
        rid = "service:port:1"
        store.upsert_resources(conn, [Resource(rid, "service", "1", "services")], now=now)
        store.append_signals(conn, [Signal(rid, now - k * 300, "up") for k in range(3)])
    conn.close()
    pill = next(p for p in build(FakeHub(HUB_STATE)).get("/api/overview").json()["health"]["pills"]
                if p["id"] == "services")
    assert pill["stale"] is False and pill["availability"] == 1.0


# ── waiting sessions and in progress (#5) ──

NOW = int(time.time())


def project(name: str, last_action: int, stage: str = "active", **extra: Any) -> Resource:
    attrs = {"name": name, "stage": stage, "stage_label": {"active": "活跃", "waiting": "等你",
                                                            "stalled": "停滞"}.get(stage, stage),
             "reason": "1 小时内有动作", "last_action": last_action, "dirty": 0, "open_prs": None,
             "sessions": {"total": 1, "recent": 1, "waiting": 0},
             "last_conclusion": {"text": "## 结论\n\n**测试都过了**，还差截图。\n\n细节……",
                                 "ts": last_action, "session": "s", "source": "claude"},
             **extra}
    return Resource(f"project:sessions:{name}", "project", name, "sessions", attrs)


def session(sid: str, state: str, last_active: int, project_name: str = "甲") -> Resource:
    attrs = {"source": "claude", "session_id": sid, "project": project_name, "state": state,
             "last_active": last_active, "last_user": "把总览接上真实数据\n第二行",
             "last_agent_ts": last_active, "waiting_reason": "Agent 最后一条是提问"}
    return Resource(f"session:claude:{sid}", "session", f"{project_name} · {sid}", "sessions",
                    attrs)


def seed(db: Path, resources: list[Resource], signals: list[Signal] = ()) -> None:
    conn = store.connect(db)
    with store.transaction(conn):
        store.upsert_resources(conn, resources, now=NOW)
        store.append_signals(conn, list(signals))
    conn.close()


def test_waiting_session_joins_needs_you_after_cards_and_warnings(make):
    build, db = make
    open_incident(db, "warning")
    seed(db, [project("甲", NOW - 60, "waiting"), session("w1", "waiting", NOW - 120),
              session("old", "waiting", NOW - 5 * 86400), session("d1", "done", NOW - 30)])
    data = build(FakeHub(HUB_STATE)).get("/api/overview").json()
    kinds = [(x["kind"], x["id"]) for x in data["attention"]["items"]]
    # card > warning incident > waiting session; a question older than 3 days no longer counts
    assert kinds == [("card", 41), ("incident", 1), ("session", "session:claude:w1")]
    s = data["attention"]["items"][2]
    assert s["title"] == "把总览接上真实数据" and s["sub"] == "甲 · Agent 最后一条是提问"
    assert s["ts"] == NOW - 120 and data["attention"]["ok"] is True


def test_inprogress_cards_are_the_most_recent_projects(make):
    build, db = make
    seed(db, [project(f"p{i}", NOW - i * 600, dirty=i) for i in range(7)]
         + [project("old", NOW - 9 * 86400, "stalled", dirty=3, open_prs=2)])
    block = build(FakeHub(HUB_STATE)).get("/api/overview").json()["inprogress"]
    assert block["ok"] and block["count"] == 8 and block["stale"] is False
    assert [c["name"] for c in block["items"]] == ["p0", "p1", "p2", "p3", "p4"]
    card0 = block["items"][1]
    assert card0["conclusion"] == "结论" and card0["dirty"] == 1 and card0["stage_label"] == "活跃"


def test_gist_takes_the_first_real_line_without_markup():
    assert ov.gist("\n> **已合并**：三处改动\n- 其余") == "已合并：三处改动"
    assert ov.gist("x" * 300, 10) == "x" * 9 + "…"
    assert ov.gist(None) == ""


def test_broken_session_board_only_empties_its_own_parts(make, monkeypatch):
    build, db = make
    open_incident(db)

    def broken(_db):
        raise RuntimeError("看板读不了")

    monkeypatch.setattr(ov, "read_board", broken)
    data = build(FakeHub(HUB_STATE)).get("/api/overview").json()
    assert [x["kind"] for x in data["attention"]["items"]] == ["card", "incident"]
    assert data["attention"]["errors"] == {"sessions": "RuntimeError: 看板读不了"}
    assert data["inprogress"] == {"ok": False, "error": "RuntimeError: 看板读不了"}
    assert data["artifacts"]["ok"] and data["health"]["ok"] and data["daily"]["ok"]


# ── recent artifacts (#6) ──


def artifact(slug: str, updated: int, *, expires: int | None = None, visibility: str = "public",
             gone: bool = False) -> Resource:
    return Resource(f"artifact:orca:{slug}", "artifact", slug, "artifacts",
                    {"title": f"页面 {slug}", "adapter": "orca", "visibility": visibility,
                     "updated_at": updated, "expires_at": expires, "project": "甲"})


def test_recent_artifacts_flag_expired_expiring_and_sensitive(make):
    build, db = make
    arts = [artifact(f"a{i}", NOW - i * 3600) for i in range(6)]
    arts[1] = artifact("a1", NOW - 3600, expires=NOW - 10)            # expired
    arts[2] = artifact("a2", NOW - 7200, expires=NOW + 86400)         # expiring in a day
    arts[5] = artifact("a5", NOW - 5 * 3600, expires=NOW - 99)        # expired, not in the top 5
    seed(db, arts, [Signal("artifact:orca:a3", NOW, "up",
                           detail={"sensitive": True, "hits": [{"label": "密钥", "count": 1}]}),
                    Signal("artifact:orca:a0", NOW, "up", detail={"sensitive": False})])
    block = build(FakeHub(HUB_STATE)).get("/api/overview").json()["artifacts"]
    assert block["ok"] and block["count"] == 6
    items = {a["id"].split(":")[-1]: a for a in block["items"]}
    assert list(items) == ["a0", "a1", "a2", "a3", "a4"]
    assert items["a1"]["expired"] and not items["a1"]["expiring"]
    assert items["a2"]["expiring"] and not items["a2"]["expired"]
    assert items["a3"]["public_sensitive"] and items["a3"]["title"] == "页面 a3"
    assert items["a0"]["sensitive"] is False and items["a4"]["sensitive"] is None
    assert (block["expired"], block["expiring"], block["public_sensitive"]) == (2, 1, 1)


def test_broken_artifacts_config_only_empties_artifacts(make):
    build, db = make
    cfg = build.vault / ".kg" / "plane" / "artifacts.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("local: [unclosed\n", encoding="utf-8")
    seed(db, [artifact("a0", NOW), project("甲", NOW)])
    data = build(FakeHub(HUB_STATE)).get("/api/overview").json()
    assert data["artifacts"]["ok"] is False and "ParserError" in data["artifacts"]["error"]
    assert data["inprogress"]["ok"] and data["inprogress"]["items"][0]["name"] == "甲"
    assert data["attention"]["ok"] and data["health"]["ok"]


# ── the index pill (#7 status.json, #9 stale mark) ──


def write_index(vault: Path, status: dict[str, Any] | str, files: dict[str, Any] | None) -> None:
    root = vault / ".kg" / "index"
    root.mkdir(parents=True, exist_ok=True)
    (root / "status.json").write_text(
        status if isinstance(status, str) else json.dumps(status, ensure_ascii=False),
        encoding="utf-8")
    if files is not None:
        (root / "manifest.json").write_text(json.dumps(
            {"model": "m", "granularity": "entry", "files": files}), encoding="utf-8")


def stamp(vault: Path, rel: str) -> dict[str, Any]:
    st = (vault / rel).stat()
    return {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "sha": "x", "entry_id": rel}


def index_pill_of(build) -> dict[str, Any]:
    pills = build(FakeHub(HUB_STATE)).get("/api/overview").json()["health"]["pills"]
    return next(p for p in pills if p["id"] == "index")


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def test_index_pill_ok_with_last_success_and_pending_files(make):
    build, _ = make
    vault = build.vault
    # a.md is indexed as it is; b.md changed since; gone.md was deleted
    write_index(vault, {"ok": True, "last_success": iso(NOW - 3600), "error": None},
                {"10-Experience/a.md": stamp(vault, "10-Experience/a.md"),
                 "10-Experience/b.md": {**stamp(vault, "10-Experience/b.md"), "size": 1},
                 "10-Experience/gone.md": {"mtime_ns": 1, "size": 1}})
    pill = index_pill_of(build)
    assert (pill["status"], pill["text"]) == ("up", "正常")
    assert pill["last"] == NOW - 3600 and pill["pending"] == 2


def test_index_pill_failed_build_is_down_with_the_error(make):
    build, _ = make
    write_index(build.vault, {"ok": False, "last_success": iso(NOW - 3600),
                              "error": {"code": "embed_failed", "message": "向量模型加载失败"}}, {})
    pill = index_pill_of(build)
    assert pill["status"] == "down" and pill["reason"] == "向量模型加载失败"
    assert pill["last"] == NOW - 3600


def test_index_pill_stale_mark_until_a_later_success(make):
    build, _ = make
    mark = {"stale": True, "since": NOW - 600, "reason": "索引事故 #3", "incident": 3}
    write_index(build.vault, {"ok": True, "last_success": iso(NOW - 3600), **mark}, {})
    pill = index_pill_of(build)
    assert (pill["status"], pill["text"], pill["reason"]) == ("stale", "过期", "索引事故 #3")
    # a successful update after the mark wins: the index is current again
    write_index(build.vault, {"ok": True, "last_success": iso(NOW - 60), **mark}, {})
    assert index_pill_of(build)["status"] == "up"


def test_index_pill_without_success_for_two_days_is_stale():
    pill = ov.index_pill({"ok": True, "last_success": iso(NOW - 3 * 86400)}, {}, 0, None, NOW)
    assert pill["status"] == "stale" and "3 天" in pill["reason"]
    assert ov.index_pill({}, {}, 0, None, NOW)["status"] == "unknown"


def test_broken_index_status_only_turns_the_index_pill_red(make):
    build, db = make
    write_index(build.vault, "{not json", {})
    data = build(FakeHub(HUB_STATE)).get("/api/overview").json()
    pills = {p["id"]: p for p in data["health"]["pills"]}
    assert data["health"]["ok"] and pills["index"]["status"] == "down"
    assert "读不了索引状态" in pills["index"]["reason"]
    assert pills["hub"]["status"] == "up" and pills["egress"]["text"] == "未知"
