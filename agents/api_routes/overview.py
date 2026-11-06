"""/api/overview: the first screen of kg-web in one request.

Each block reads its own source and fails on its own: when the hub is down, the incidents, the
health of local resources and today's daily brief still come back, and the hub's parts say why
they are empty. The ranking, the health pills and the staleness check are pure functions below,
so tests drive them with plain lists.

Sessions waiting for you and the in-progress cards come from the #5 board, recent artifacts
from #6, the index pill from the #7 index files plus the #9 stale mark. Each is read by calling
those modules' functions directly, never over HTTP. A block whose module is not built yet answers
`{"planned": <issue>}`; the page shows "规划中 #n".
"""

from __future__ import annotations

import re
import statistics
import time
from datetime import datetime
from pathlib import Path  # noqa: TC003 - FastAPI resolves the annotations at runtime
from typing import TYPE_CHECKING, Annotated, Any

import yaml
from fastapi import APIRouter, Depends

from agents.api_routes.hub import get_client
from agents.api_routes.plane import plane_db_path
from agents.autoloop import HubClient, HubError
from agents.local_guard import require_local

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

router = APIRouter(prefix="/overview", tags=["overview"], dependencies=[Depends(require_local)])

DAY = 86400
DEFAULT_PERIOD = 300  # an unknown collector is assumed to run every 5 minutes
MIN_PERIOD = 60
STALE_PERIODS = 2

# ── Ranking what needs you ──────────────────────────────────────────────────────────────────────

# Lower comes first: a critical incident beats an open card, a card beats a warning, and a
# session waiting for an answer comes last (it waits for you, nothing is breaking).
URGENCY = {"critical": 0, "card": 1, "warning": 2, "info": 3, "session": 4}


def attention_items(cards: Iterable[Mapping[str, Any]], incidents: Iterable[Mapping[str, Any]],
                    sessions: Iterable[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """Open hub cards, open incidents and waiting sessions as one list, most urgent first.

    Within one urgency the oldest comes first: it has waited longest.
    """
    items: list[dict[str, Any]] = []
    for inc in incidents:
        if inc.get("closed_at"):
            continue
        sev = str(inc.get("severity") or "warning")
        items.append({
            "kind": "incident", "urgency": sev if sev in URGENCY else "warning",
            "id": inc.get("id"), "title": inc.get("summary") or inc.get("rule") or "未命名事故",
            "sub": f"{inc.get('resource_id', '')} · {inc.get('rule', '')}",
            "ts": int(inc.get("opened_at") or 0),
        })
    for card in cards:
        items.append({
            "kind": "card", "urgency": "card", "id": card.get("id"),
            "title": card.get("title") or f"卡片 #{card.get('id')}",
            "sub": card.get("rule") or "", "ask": card.get("kind") == "ask",
            "ts": _unix(card.get("createdAt")),
        })
    for s in sessions:
        items.append({
            "kind": "session", "urgency": "session", "id": s.get("id"),
            "title": s.get("title") or "等你回答的会话", "sub": s.get("project") or "",
            "ts": _unix(s.get("since")),
        })
    items.sort(key=lambda x: (URGENCY[x["urgency"]], x["ts"], str(x["id"])))
    return items


def _unix(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value:
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0
    return 0


# ── Health pills ────────────────────────────────────────────────────────────────────────────────

WORDS = {"up": "正常", "down": "异常", "asleep": "休眠", "unknown": "未知", "stale": "过期"}


def group_status(statuses: Iterable[str | None]) -> str:
    """Latest statuses of a group → up / down / asleep / unknown for one pill.

    Any down or degraded member makes the pill 异常; all asleep is 休眠; otherwise any up is 正常.
    """
    seen = [s or "unknown" for s in statuses]
    if any(s in ("down", "degraded") for s in seen):
        return "down"
    if seen and all(s == "asleep" for s in seen):
        return "asleep"
    if any(s == "up" for s in seen):
        return "up"
    return "unknown"


def availability(signals: Iterable[Any]) -> float | None:
    """Share of up readings among readings that say up or not (asleep / unknown don't count).

    Hourly rollups count as `samples` readings. None when nothing in the window says either way.
    """
    up = total = 0
    for s in signals:
        weight = max(int(getattr(s, "samples", 0) or 0), 1)
        if s.status == "up":
            up += weight
            total += weight
        elif s.status in ("down", "degraded"):
            total += weight
    return round(up / total, 4) if total else None


def infer_period(timestamps: Iterable[int], default: int = DEFAULT_PERIOD) -> int:
    """How often a collector reports: the median gap between its distinct report times."""
    ts = sorted(set(int(t) for t in timestamps))
    gaps = [b - a for a, b in zip(ts, ts[1:], strict=False) if b > a]
    if not gaps:
        return default
    return max(int(statistics.median(gaps)), MIN_PERIOD)


def is_stale(last: int | None, period: int, now: int) -> bool:
    """True when the collector has missed more than two reports (or never reported)."""
    if not last:
        return True
    return now - last > STALE_PERIODS * period


def health_pill(pid: str, label: str, resources: list[Mapping[str, Any]],
                signals: Mapping[str, list[Any]], now: int, *,
                planned: int | None = None) -> dict[str, Any]:
    """One pill from a group of resources and their last 24 hours of signals."""
    pill: dict[str, Any] = {"id": pid, "label": label, "count": len(resources)}
    if not resources:
        if planned:
            return {**pill, "status": "planned", "issue": planned, "text": f"规划中 #{planned}"}
        return {**pill, "status": "unknown", "text": "未知", "reason": "还没有采集数据"}
    status = group_status(r.get("status") for r in resources)
    bad = [r["name"] or r["id"] for r in resources if r.get("status") in ("down", "degraded")]
    flat = [s for r in resources for s in signals.get(r["id"], [])]
    last = max((int(r.get("status_ts") or r.get("last_seen") or 0) for r in resources), default=0)
    period = infer_period(s.ts for s in flat)
    return {
        **pill, "status": status, "text": WORDS[status], "down": bad[:5],
        "availability": availability(flat), "last": last or None, "period": period,
        "stale": is_stale(last, period, now),
    }


def _matches(rid: str, patterns: list[str]) -> bool:
    return any(rid == p or rid.startswith(p) for p in patterns)


def _plane_config(vault: Path) -> dict[str, Any]:
    file = vault / ".kg" / "plane" / "config.yaml"
    if not file.exists():
        return {}
    raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    return raw.get("overview") or {}


def plane_health(db: Path, vault: Path, now: int) -> list[dict[str, Any]]:
    """Egress, key local services and the index. The first two come from the state store."""
    from kg.plane.store import connect, list_resources, list_signals

    cfg = _plane_config(vault)
    key = [str(p) for p in cfg.get("key_services") or []]
    conn = connect(db if db.exists() else ":memory:")
    try:
        egress = list_resources(conn, kind="egress")
        services = list_resources(conn, kind="service")
        if key:
            services = [r for r in services if _matches(r["id"], key)]
        out = []
        for pid, label, res in (("egress", "出口", egress), ("services", "本地服务", services)):
            sig = {r["id"]: list_signals(conn, r["id"], since=now - DAY) for r in res}
            out.append(health_pill(pid, label, res, sig, now))
    finally:
        conn.close()
    out.append(index_pill_safe(vault, now))
    return out


# ── The index pill (#7 index files, #9 stale mark) ──────────────────────────────────────────────

INDEX_MAX_AGE = 2 * DAY  # the index is updated nightly; two days without a success is 过期


def index_pill(status: Mapping[str, Any], mark: Mapping[str, Any], pending: int | None,
               missing: str | None, now: int) -> dict[str, Any]:
    """正常 / 异常 / 过期 for the search index.

    `status` is `.kg/index/status.json` as the index writes it (ok, last_success, error),
    `mark` the #9 stale mark in the same file (stale, since, reason), `pending` the number of
    files that changed since the last build, `missing` why there is no index at all.

    异常: no index, or the last attempt failed. 过期: marked stale by an incident and not rebuilt
    successfully since, or no success for two days. Otherwise 正常, even with files pending:
    they wait for the next nightly update.
    """
    pill: dict[str, Any] = {"id": "index", "label": "索引", "availability": None,
                            "stale": False, "pending": pending}
    last = _unix(status.get("last_success")) or None
    pill["last"] = last
    error = status.get("error") if isinstance(status.get("error"), dict) else None
    if missing:
        return {**pill, "status": "down", "text": WORDS["down"], "reason": missing}
    if status.get("ok") is False:
        why = (error or {}).get("message") or "上次建索引失败"
        return {**pill, "status": "down", "text": WORDS["down"], "reason": str(why)[:200]}
    since = int(mark.get("since") or 0)
    if mark.get("stale") and (last is None or last < since):
        return {**pill, "status": "stale", "text": WORDS["stale"],
                "reason": str(mark.get("reason") or "事故把索引标成过期")[:200]}
    if last is None:
        return {**pill, "status": "unknown", "text": WORDS["unknown"],
                "reason": "还没有成功建过索引的记录"}
    if now - last > INDEX_MAX_AGE:
        return {**pill, "status": "stale", "text": WORDS["stale"],
                "reason": f"{(now - last) // DAY} 天没有成功更新"}
    return {**pill, "status": "up", "text": WORDS["up"]}


def read_index(vault: Path) -> tuple[dict[str, Any], dict[str, Any], int | None, str | None]:
    """What `kg index status --json` reads: status.json, the stale mark and the pending files."""
    from kg.plane.heal.index_status import read_index_status
    from kg.retrieval.errors import RetrievalError
    from kg.retrieval.index import Index

    index = Index(vault)
    status = index.status()
    mark = read_index_status(vault).__dict__
    try:
        pending: int | None = index.pending().count
        missing = None
    except RetrievalError as exc:
        pending, missing = None, str(exc)
    return status, mark, pending, missing


def index_pill_safe(vault: Path, now: int) -> dict[str, Any]:
    """The index pill; a broken status file turns only this pill red, not the health strip."""
    try:
        return index_pill(*read_index(vault), now)
    except Exception as exc:
        return {"id": "index", "label": "索引", "availability": None, "stale": False,
                "status": "down", "text": WORDS["down"],
                "reason": f"读不了索引状态：{type(exc).__name__}: {exc}"[:400]}


def hub_pill(state: Mapping[str, Any] | None, error: str | None) -> dict[str, Any]:
    pill: dict[str, Any] = {"id": "hub", "label": "自动化中台", "availability": None}
    if error is not None or state is None:
        return {**pill, "status": "down", "text": "异常", "reason": error or "没有返回"}
    agents = state.get("agents") or []
    off = [a.get("name") or a.get("id") for a in agents if not a.get("online")]
    status = "up" if not off else "down"
    note = (f"{len(agents)} 个 Agent 都在线" if not off
            else f"{len(off)}/{len(agents)} 个 Agent 离线")
    return {**pill, "status": status, "text": WORDS[status], "down": off[:5],
            "count": len(agents), "stale": False, "note": note}


# ── Today ───────────────────────────────────────────────────────────────────────────────────────

DAILY_DIR = Path("00-Index") / "Daily"
_LEAD = re.compile(r"^>\s*(?:一句话[：:]\s*)?(.+)$", re.M)
_CREATED = re.compile(r"^created:\s*['\"]?(\d{4}-\d{2}-\d{2})", re.M)


def daily_lead(vault: Path, today: str) -> dict[str, Any]:
    folder = vault / DAILY_DIR
    dates = sorted((f.stem for f in folder.glob("*.md")), reverse=True) if folder.is_dir() else []
    if not dates:
        return {"date": None, "today": False, "lead": ""}
    date = today if today in dates else dates[0]
    head = (folder / f"{date}.md").read_text(encoding="utf-8", errors="replace")[:4000]
    m = _LEAD.search(head)
    return {"date": date, "today": date == today, "lead": m.group(1).strip() if m else ""}


def fresh_today(vault: Path, today: str, start: float) -> int:
    """Entries created today. Only files touched today are opened, so this stays cheap."""
    from agents.utils import VAULT_ENTRY_DIRS

    n = 0
    for name in VAULT_ENTRY_DIRS:
        folder = vault / name
        if not folder.is_dir():
            continue
        for md in folder.rglob("*.md"):
            try:
                if md.stat().st_mtime < start:
                    continue
                with md.open(encoding="utf-8", errors="replace") as fh:
                    head = fh.read(3000)
            except OSError:
                continue
            m = _CREATED.search(head)
            if m and m.group(1) == today:
                n += 1
    return n


def automation_today(state: Mapping[str, Any]) -> dict[str, Any]:
    totals = state.get("totals") or {}
    today = (state.get("spending") or {}).get("today") or {}
    return {"rounds": totals.get("doneToday", 0), "running": totals.get("running", 0),
            "cost": today.get("cost", 0), "missingCost": today.get("missingCost", 0)}


# ── In progress (#5) and recent artifacts (#6) ──────────────────────────────────────────────────

INPROGRESS_LIMIT = 5
ARTIFACTS_LIMIT = 5
EXPIRING = 3 * DAY  # same window as the artifact center's "即将过期"
_MARKUP = re.compile(r"^[\s#>*\-`|]+|[*`]+")


def gist(text: str | None, limit: int = 120) -> str:
    """First non-empty line of an agent reply, without markdown marks: its conclusion."""
    for line in (text or "").splitlines():
        line = _MARKUP.sub("", line).strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def waiting_sessions(board: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The board's sessions waiting for an answer, shaped for `attention_items`."""
    out = []
    for s in (board.get("attention") or {}).get("waiting") or []:
        first = (s.get("last_user") or "").strip().splitlines()
        title = gist(first[0] if first else "", 60) or f"会话 {str(s.get('session_id', ''))[:8]}"
        sub = " · ".join(x for x in (s.get("project"), s.get("waiting_reason")) if x)
        out.append({"id": s.get("id"), "title": title, "project": sub,
                    "since": s.get("last_agent_ts") or s.get("last_active")})
    return out


def project_cards(board: Mapping[str, Any], limit: int = INPROGRESS_LIMIT) -> dict[str, Any]:
    """The most recently active projects of the board, newest first."""
    projects = sorted(board.get("items") or [], key=lambda p: -(p.get("last_action") or 0))
    cards = []
    for p in projects[:limit]:
        last = p.get("last_conclusion") or {}
        cards.append({
            "id": p.get("id"), "name": p.get("name"), "stage": p.get("stage"),
            "stage_label": p.get("stage_label"), "reason": p.get("reason") or "",
            "last_action": p.get("last_action"),
            "conclusion": gist(last.get("text")), "conclusion_ts": last.get("ts"),
            "dirty": int(p.get("dirty") or 0), "open_prs": p.get("open_prs"),
            "waiting": int((p.get("sessions") or {}).get("waiting") or 0),
        })
    return {"items": cards, "count": len(projects),
            "stale": bool(board.get("stale")), "collected_at": board.get("collected_at")}


def recent_artifacts(listing: Mapping[str, Any], now: int,
                     limit: int = ARTIFACTS_LIMIT) -> dict[str, Any]:
    """The newest artifacts with 已过期 / 即将过期 / 敏感 flags, and counts over all of them."""
    items = [a for a in listing.get("items") or [] if not a.get("gone_at")]

    def flags(a: Mapping[str, Any]) -> dict[str, Any]:
        attrs = a.get("attrs") or {}
        expires = attrs.get("expires_at")
        public = attrs.get("visibility") == "public"
        return {"expires_at": expires, "expired": bool(expires and expires <= now),
                "expiring": bool(expires and now < expires <= now + EXPIRING),
                "sensitive": a.get("sensitive"), "public_sensitive": bool(a.get("sensitive"))
                and public}

    out = []
    for a in items[:limit]:
        attrs = a.get("attrs") or {}
        out.append({
            "id": a.get("id"), "title": attrs.get("title") or a.get("name") or a.get("id"),
            "adapter": attrs.get("adapter"), "visibility": attrs.get("visibility"),
            "project": attrs.get("project"),
            "updated": attrs.get("updated_at") or a.get("first_seen"),
            "incidents": len(a.get("incidents") or []), **flags(a),
        })
    every = [flags(a) for a in items]
    return {"items": out, "count": len(items),
            "expired": sum(f["expired"] for f in every),
            "expiring": sum(f["expiring"] for f in every),
            "sensitive": sum(bool(f["sensitive"]) for f in every),
            "public_sensitive": sum(f["public_sensitive"] for f in every)}


def read_board(db: Path) -> dict[str, Any]:
    from agents.api_routes.sessions import get_projects

    return get_projects(db)


def read_artifacts(db: Path, vault: Path) -> dict[str, Any]:
    from agents.api_routes.artifacts import get_artifacts
    from kg.plane.collectors.artifacts import load_artifacts_config

    return get_artifacts(db, load_artifacts_config(vault), gone=False)


# ── The endpoint ────────────────────────────────────────────────────────────────────────────────


def overview_vault() -> Path:
    from agents.config import load_config

    return load_config().vault_path


def _why(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:400]


def _block(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"ok": True, **fn()}
    except Exception as exc:  # one block failing must not take the page down
        return {"ok": False, "error": _why(exc)}


def _fetch(fn: Callable[[], Any]) -> tuple[Any, str | None]:
    try:
        return fn(), None
    except Exception as exc:
        return None, _why(exc)


@router.get("")
def overview(
    client: Annotated[HubClient, Depends(get_client)],
    db: Annotated[Path, Depends(plane_db_path)],
    vault: Annotated[Path, Depends(overview_vault)],
) -> dict[str, Any]:
    now = int(time.time())
    local = datetime.now().astimezone()
    today = local.strftime("%Y-%m-%d")
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    hub_state: dict[str, Any] | None = None
    hub_error: str | None = None
    try:
        hub_state = client.call("GET", "/v1/state")
    except HubError as exc:
        hub_error = exc.message
    except Exception as exc:
        hub_error = _why(exc)

    incidents: list[dict[str, Any]] | None = None
    incident_error: str | None = None
    try:
        from kg.plane.store import connect, list_incidents

        conn = connect(db if db.exists() else ":memory:")
        try:
            incidents = list_incidents(conn, open_only=True, limit=200)
        finally:
            conn.close()
    except Exception as exc:
        incident_error = _why(exc)

    # Read once: waiting sessions go to "needs you", the projects to "in progress".
    board, board_error = _fetch(lambda: read_board(db))

    errors = {k: v for k, v in (("hub", hub_error), ("incidents", incident_error),
                                ("sessions", board_error)) if v}
    items = attention_items((hub_state or {}).get("cards") or [], incidents or [],
                            waiting_sessions(board) if board else [])
    attention = {"ok": not errors, "items": items[:20], "count": len(items), "errors": errors}

    health = _block(
        lambda: {"pills": [*plane_health(db, vault, now), hub_pill(hub_state, hub_error)]})

    def automation() -> dict[str, Any]:
        if hub_state is None:
            raise RuntimeError(f"读不到中台：{hub_error}")
        return automation_today(hub_state)

    return {
        "now": now,
        "today": today,
        "attention": attention,
        "health": health,
        "incidents": ({"ok": True, "open": len(incidents)} if incidents is not None
                      else {"ok": False, "error": incident_error}),
        "daily": _block(lambda: daily_lead(vault, today)),
        "automation": _block(automation),
        "fresh": _block(lambda: {"count": fresh_today(vault, today, start)}),
        "inprogress": ({"ok": True, **project_cards(board)} if board is not None
                       else {"ok": False, "error": board_error}),
        "artifacts": _block(lambda: recent_artifacts(read_artifacts(db, vault), now)),
    }
