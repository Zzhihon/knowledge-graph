"""The search index as a state-store resource: `index:kg:main` signals and rule `index.failed`."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from kg.plane import store
from kg.plane.collectors.index_build import (
    RESOURCE_ID,
    BuildOutcome,
    build_signal,
    index_failed,
    record_build,
)
from kg.plane.heal import audit
from kg.plane.heal.actions import HealContext, on_close, on_open
from kg.plane.heal.config import parse_config
from kg.plane.heal.index_status import read_index_status, search_warning
from kg.plane.heal.retro import get_incident
from kg.plane.ids import make_id, parse_id
from kg.plane.rules import all_rules, replay
from kg.plane.store import Signal

if TYPE_CHECKING:
    from pathlib import Path

T0 = 1_780_000_000
DAY = 86_400


def _rule():
    (r,) = [r for r in all_rules() if r.name == "index.failed"]
    return r


def _series(pattern: str) -> list[Signal]:
    """u = up, g = degraded, d = down, ? = unknown; one run a day."""
    status = {"u": "up", "g": "degraded", "d": "down", "?": "unknown"}
    out = []
    for i, ch in enumerate(pattern):
        detail = {"action": "update"}
        if ch == "d":
            detail.update(code="embedding_failed", message="模型加载失败")
        out.append(Signal(RESOURCE_ID, T0 + i * DAY, status[ch], 1.0, "s", detail))
    return out


def test_index_resource_id_follows_the_id_rules() -> None:
    assert RESOURCE_ID == "index:kg:main" == make_id("index", "kg", "main")
    assert parse_id(RESOURCE_ID) == ("index", ("kg", "main"))
    r = _rule()
    assert (r.kind, r.window) == ("index", 1)


def test_first_failure_opens_and_next_good_run_closes() -> None:
    #                        0123456
    assert replay(_rule(), _series("uudddud")) == [
        ("open", T0 + 2 * DAY), ("close", T0 + 5 * DAY), ("open", T0 + 6 * DAY)]


def test_degraded_run_closes_and_unknown_keeps_it_open() -> None:
    assert replay(_rule(), _series("d??g")) == [("open", T0), ("close", T0 + 3 * DAY)]
    assert replay(_rule(), _series("ugug")) == []


def test_verdict_is_a_warning_with_the_error_code() -> None:
    verdict = index_failed(_series("d"))
    assert verdict is not None and verdict.firing and verdict.severity == "warning"
    assert verdict.summary == "检索索引更新失败（embedding_failed）"
    assert verdict.evidence == {"ts": T0, "code": "embedding_failed", "message": "模型加载失败"}
    assert index_failed([Signal(RESOURCE_ID, T0, "down", detail=None)]).evidence["code"] == (
        "index_failed")


def test_build_signal_status_and_detail() -> None:
    ok = BuildOutcome("update", True, seconds=1.5, model="m", counts={"added": 2})
    assert build_signal(ok, T0) == Signal(RESOURCE_ID, T0, "up", 1.5, "s",
                                          {"action": "update", "model": "m", "added": 2})
    partial = BuildOutcome("update", True, seconds=1.5, errors=["a.md: 读不了"] * 7)
    sig = build_signal(partial, T0)
    assert sig.status == "degraded" and sig.detail["error_count"] == 7
    assert len(sig.detail["errors"]) == 5
    bad = build_signal(BuildOutcome("rebuild", False, "index_missing", "没建"), T0)
    assert bad.status == "down" and bad.value is None
    assert bad.detail == {"action": "rebuild", "model": "", "code": "index_missing",
                          "message": "没建"}


def test_record_build_opens_once_and_closes_on_success(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    fail = BuildOutcome("update", False, "embedding_failed", "模型加载失败", model="m")
    first = record_build(vault, fail, now=T0)
    assert [(c.action, c.rule, c.resource_id) for c in first.changes] == [
        ("open", "index.failed", RESOURCE_ID)]
    assert record_build(vault, fail, now=T0 + 60).changes == []  # still open, no second one
    closed = record_build(vault, BuildOutcome("update", True, seconds=2.0), now=T0 + 120)
    assert [c.action for c in closed.changes] == ["close"]
    assert closed.changes[0].incident_id == first.changes[0].incident_id

    conn = store.connect(store.db_path(vault))
    try:
        (res,) = store.list_resources(conn, kind="index")
        assert (res["id"], res["source"], res["status"]) == (RESOURCE_ID, "index", "up")
        statuses = [s.status for s in store.list_signals(conn, RESOURCE_ID)]
        assert statuses == ["down", "down", "up"]
        (inc,) = store.list_incidents(conn, resource_id=RESOURCE_ID)
        assert inc["rule"] == "index.failed" and inc["closed_at"] == T0 + 120
        assert inc["evidence"]["code"] == "embedding_failed"
    finally:
        conn.close()


def _incident(vault: Path, iid: int) -> tuple[dict, dict]:
    conn = store.connect(store.db_path(vault))
    try:
        return get_incident(conn, iid), store.get_resource(conn, RESOURCE_ID)
    finally:
        conn.close()


@pytest.mark.parametrize("mode", ["suggest", "auto"])
def test_heal_marks_the_index_stale_for_an_index_failed_incident(tmp_path: Path,
                                                                 mode: str) -> None:
    vault = tmp_path / "vault"
    first = record_build(vault, BuildOutcome("update", False, "index_missing", "没建"), now=T0)
    inc, resource = _incident(vault, first.changes[0].incident_id)
    ctx = HealContext(vault, parse_config({"actions": {"index_stale": mode}}), T0)
    (out,) = on_open(ctx, inc, resource)
    (entry,) = audit.read(vault)
    assert (out.action, entry.target) == ("index_stale", RESOURCE_ID)
    if mode == "suggest":
        assert out.option == "照建议做：标记索引过期" and search_warning(vault) is None
        return
    assert read_index_status(vault).stale and "结果可能不全" in search_warning(vault)
    closed = record_build(vault, BuildOutcome("update", True, seconds=1.0), now=T0 + 60)
    inc, resource = _incident(vault, closed.changes[0].incident_id)
    on_close(ctx, inc)
    assert search_warning(vault) is None


# ── kg index update --autoloop ──


@pytest.fixture
def cli_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    vault = tmp_path / "vault"
    (vault / "00-Index").mkdir(parents=True)
    monkeypatch.setattr("kg.infrastructure.config.loader.load_config",
                        lambda: SimpleNamespace(vault_path=vault))
    monkeypatch.delenv("KG_INDEX_DIR", raising=False)
    return vault


def _run(capsys: pytest.CaptureFixture[str], **kw) -> tuple[str, int]:  # type: ignore[no-untyped-def]
    from rich.console import Console

    from kg.interfaces.cli.commands.index import run_index_command

    code = 0
    try:
        run_index_command(action="update", granularity=None, model=None, download=False,
                          as_json=False, console=Console(stderr=True), **kw)
    except SystemExit as exc:
        code = int(exc.code or 0)
    return capsys.readouterr().out, code


def _receipt(out: str) -> list[dict]:
    block = out.split("```agent-autoloop\n", 1)[1].split("\n```", 1)[0]
    return json.loads(block)["events"]


def test_autoloop_failure_emits_opened_and_exits_zero(cli_vault, capsys) -> None:
    # No index built yet: update fails with index_missing.
    out, code = _run(capsys, autoloop=True)
    assert code == 0
    assert out.startswith("索引更新失败（index_missing）")
    (event,) = _receipt(out)
    assert event["type"] == "plane.incident.opened" and event["subject"] == RESOURCE_ID
    assert event["id"].startswith("plane-incident-") and event["id"].endswith("-opened")

    again, code = _run(capsys, autoloop=True)  # still failing: no new event, no notification
    assert code == 0 and again.startswith("没事可做：索引更新失败") and "```" not in again


def test_autoloop_success_closes_it(cli_vault, capsys, monkeypatch) -> None:
    from kg.retrieval.index import Index, UpdateReport

    _run(capsys, autoloop=True)
    monkeypatch.setattr(Index, "manifest", lambda self: {"model": "BAAI/bge-m3"})
    monkeypatch.setattr(Index, "update", lambda self, embedder, **kw: UpdateReport(
        "update", added=1, entries=10, seconds=0.5))
    out, code = _run(capsys, autoloop=True)
    assert code == 0 and out.startswith("索引更新完成：新增 1")
    (event,) = _receipt(out)
    assert event["type"] == "plane.incident.closed"
    quiet, _ = _run(capsys, autoloop=True)
    assert quiet.startswith("没事可做：索引更新完成")


def test_plain_update_keeps_its_exit_code_and_still_records(cli_vault, capsys) -> None:
    out, code = _run(capsys)
    assert code == 2  # EXIT_MISSING, as before
    conn = store.connect(store.db_path(cli_vault))
    try:
        assert [i["rule"] for i in store.list_incidents(conn, open_only=True)] == [
            "index.failed"]
    finally:
        conn.close()


def test_autoloop_exits_nonzero_when_the_store_cannot_be_written(cli_vault, capsys,
                                                                  monkeypatch) -> None:
    def broken(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("kg.plane.collectors.index_build.record_build", broken)
    out, code = _run(capsys, autoloop=True)
    assert code == 2 and "写状态库失败" in out
