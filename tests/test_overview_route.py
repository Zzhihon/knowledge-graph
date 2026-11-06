"""/api/overview: what needs you comes first, and one broken source only empties its own block.

The hub is a fake client and the state store a tmp file; the real hub and vault are never touched.
"""

from __future__ import annotations

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
def make(tmp_path: Path):
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
    assert pills["index"]["text"] == "规划中 #7"
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
