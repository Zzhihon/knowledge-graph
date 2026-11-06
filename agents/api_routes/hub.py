"""/api/hub/*: the agent-autoloop hub for the web UI, without handing the browser its owner key.

Reads go to the hub as they are; writes (answering a card, editing rules) only from this page:
see agents/local_guard.py. When the hub says no, its own words go back to the page unchanged.
Rules management lives here too: check before write, back up, roll back, and an audit trail.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agents.autoloop import (
    CheckUnavailableError,
    CliChecker,
    ControlUnsupportedError,
    HubClient,
    HubControl,
    HubError,
    HubSettings,
    RuleControl,
    RulesInvalidError,
    RulesStore,
    StaleVersionError,
    control_summary,
    load_settings,
)
from agents.local_guard import require_local, require_same_origin_json

router = APIRouter(prefix="/hub", tags=["hub"], dependencies=[Depends(require_local)])
write = [Depends(require_same_origin_json)]


def _root() -> Path:
    try:
        from agents.config import load_config

        return load_config().vault_path
    except Exception:
        return Path.cwd()


@lru_cache(maxsize=1)
def _default_settings() -> HubSettings:
    return load_settings(_root())


def get_settings() -> HubSettings:
    return _default_settings()


Settings = Annotated[HubSettings, Depends(get_settings)]


def get_client(settings: Settings) -> HubClient:
    return HubClient(settings)


_stores: dict[HubSettings, RulesStore] = {}


def get_store(settings: Settings) -> RulesStore:
    # One store per settings, so its write lock is shared by every request.
    if settings not in _stores:
        _stores[settings] = RulesStore(
            settings.rules_file, settings.audit_file, CliChecker(settings.cli)
        )
    return _stores[settings]


def get_control(client: Annotated[HubClient, Depends(get_client)]) -> RuleControl:
    return HubControl(client)


Client = Annotated[HubClient, Depends(get_client)]
Store = Annotated[RulesStore, Depends(get_store)]
Ctl = Annotated[RuleControl, Depends(get_control)]


def _error(status: int, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def _hub_error(exc: HubError) -> JSONResponse:
    return _error(exc.status, exc.message, source="hub")


# ── Proxy ───────────────────────────────────────────────────────────────────────────────────────


@router.get("/state")
def state(
    client: Client,
    store: Store,
    before: Annotated[int | None, Query(gt=0)] = None,
) -> Any:
    """The hub's /v1/state, plus each scheduled rule's cron and whether rule control works."""
    try:
        data = client.call("GET", "/v1/state" if before is None else f"/v1/state?before={before}")
    except HubError as exc:
        return _hub_error(exc)
    schedules: dict[str, str] = {}
    try:
        rules = json.loads(store.rules_file.read_text(encoding="utf-8")).get("rules", [])
        schedules = {r["name"]: r["when"]["cron"] for r in rules if "cron" in r.get("when", {})}
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        pass
    data["schedules"] = schedules
    data["control"] = control_summary(data)
    return data


@router.get("/runs/{run_id}/why")
def why(run_id: int, client: Client) -> Any:
    if run_id <= 0:
        return _error(400, "编号不正确")
    try:
        return client.call("GET", f"/v1/runs/{run_id}/why")
    except HubError as exc:
        return _hub_error(exc)


class Answer(BaseModel):
    choice: int | None = Field(None, ge=0, le=50)
    text: str | None = Field(None, max_length=4000)


@router.post("/cards/{card_id}/answer", dependencies=write)
def answer(card_id: int, body: Answer, client: Client) -> Any:
    text = body.text.strip() if body.text else None
    if (body.choice is None) == (not text):
        return _error(400, "选一个选项，或者写一句话（二选一）")
    payload: dict[str, Any] = {"choice": body.choice} if body.choice is not None else {"text": text}
    if card_id <= 0:
        return _error(400, "编号不正确")
    try:
        return client.call("POST", f"/v1/cards/{card_id}/answer", payload)
    except HubError as exc:
        return _hub_error(exc)


# ── Rules ───────────────────────────────────────────────────────────────────────────────────────


def _refusal(exc: Exception) -> JSONResponse:
    if isinstance(exc, RulesInvalidError):
        return _error(422, "校验没通过，没有写入", issues=exc.issues)
    if isinstance(exc, StaleVersionError):
        return _error(409, str(exc))
    if isinstance(exc, CheckUnavailableError):
        return _error(503, str(exc))
    raise exc


@router.get("/rules")
def rules(store: Store) -> Any:
    try:
        return store.read()
    except FileNotFoundError:
        return _error(404, f"找不到规则文件：{store.rules_file}")


@router.get("/rules/audit")
def audit(store: Store, limit: Annotated[int, Query(ge=1, le=500)] = 50) -> Any:
    return {"entries": store.audit(limit)}


class RuleEdit(BaseModel):
    original: str | None = None
    rule: dict[str, Any]
    version: str = ""


class TextEdit(BaseModel):
    text: str = Field(..., max_length=1_000_000)
    version: str = ""


class Rollback(BaseModel):
    version: str
    backup: str | None = None


class Check(BaseModel):
    text: str | None = Field(None, max_length=1_000_000)
    original: str | None = None
    rule: dict[str, Any] | None = None


@router.post("/rules/check", dependencies=write)
def check(body: Check, store: Store) -> Any:
    """Dry run: what `agent-autoloop check` says about a candidate. Never writes."""
    try:
        text = body.text if body.rule is None else store.rule_candidate(body.original, body.rule)[0]
        if text is None:
            return _error(400, "要给 text 或 rule")
        issues = store.validate(text)
    except (RulesInvalidError, CheckUnavailableError) as exc:
        return _refusal(exc)
    return {"ok": not issues, "issues": issues}


@router.post("/rules/save", dependencies=write)
def save_rule(body: RuleEdit, store: Store) -> Any:
    """Create a rule (original empty) or replace one; the whole file is checked before writing."""
    try:
        text, _ = store.rule_candidate(body.original, body.rule)
        action = "create" if body.original is None else "edit"
        name = body.rule.get("name", "")
        note = f"新建「{name}」" if action == "create" else f"修改「{body.original}」"
        return store.write(text, body.version, action, note)
    except (RulesInvalidError, StaleVersionError, CheckUnavailableError) as exc:
        return _refusal(exc)


@router.put("/rules/text", dependencies=write)
def save_text(body: TextEdit, store: Store) -> Any:
    """Replace the whole file with hand-written JSON, under the same check."""
    try:
        return store.write(body.text, body.version, "edit-file", "直接改整个文件")
    except (RulesInvalidError, StaleVersionError, CheckUnavailableError) as exc:
        return _refusal(exc)


@router.post("/rules/rollback", dependencies=write)
def rollback(body: Rollback, store: Store) -> Any:
    try:
        return store.rollback(body.version, body.backup)
    except (RulesInvalidError, StaleVersionError, CheckUnavailableError) as exc:
        return _refusal(exc)


class Control(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    action: str = Field(..., pattern="^(pause|resume|run)$")
    scope: str = Field("rule", pattern="^(rule|requires)$")
    reason: str = Field("", max_length=500)
    until: str | None = Field(None, max_length=40)
    subject: str = Field("", max_length=500)


@router.post("/rules/control", dependencies=write)
def control(body: Control, ctl: Ctl) -> Any:
    """Pause, resume or run a rule now, or pause / resume every rule that requires a capability.

    The hub keeps these as facts in its log; rules.json is not touched.
    """
    reason = body.reason.strip()
    try:
        if body.scope == "requires":
            if body.action == "run":
                return _error(400, "能力没法立即运行，只能暂停或恢复写了它的规则")
            if body.action == "pause":
                return ctl.pause_requires(body.name, reason, body.until)
            return ctl.resume_requires(body.name)
        if body.action == "pause":
            return ctl.pause(body.name, reason, body.until)
        if body.action == "resume":
            return ctl.resume(body.name)
        return ctl.run_now(body.name, reason, body.subject.strip())
    except ControlUnsupportedError as exc:
        return _error(501, str(exc), upgrade=True)
    except HubError as exc:
        return _hub_error(exc)
