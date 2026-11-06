"""What the MCP tools answer, as plain functions over a `Plane` (vault + state store + search).

Every function is read-only and returns a JSON-like dict; the server (`kg.mcp.server`) passes it
through `kg.mcp.scrub` before it leaves the process. Failures come back as
`{"error": {"code", "message", "hint"}}` next to whatever could still be answered, never as an
empty list that looks like "nothing found".

The data comes from the same functions the board uses, so an agent and kg-web agree:

| tool                | source                                                                 |
|---------------------|------------------------------------------------------------------------|
| search_knowledge    | `kg.retrieval` (#7): BM25 + vectors fused by RRF, local rerank optional |
| get_entry           | the entry file in the vault                                            |
| project_status      | `agents.api_routes.sessions.get_projects` (`/api/plane/projects`, #5)   |
| recent_sessions     | `agents.api_routes.sessions.get_sessions` (`/api/plane/sessions`)       |
| plane_health        | `agents.api_routes.overview.plane_health` + open incidents + index      |
| find_artifact       | artifact resources (#6) and their local text (`kg.plane.artifact_docs`) |
| similar_incidents   | incidents in the state store (#9), their retro entries, debug entries   |
"""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from kg.retrieval.search import Result

    SearchFn = Callable[[str, int, dict[str, str] | None, bool], Result]

DAY = 86400
BODY_CHARS = 30_000  # get_entry: longer bodies are cut here (`truncated: true`)
TEXT_CHARS = 600  # conclusions and last messages in project / session views
ARTIFACT_TEXT_BYTES = 300_000

# Boards as the knowledge base names them (kg-distill), plus the plain folder groups.
BOARDS: dict[str, tuple[str, ...]] = {
    "项目": ("07-Projects",),
    "通识": ("01-Principles", "02-Patterns", "04-Architecture", "05-Research"),
    "经验": ("10-Experience",),
    "实验": ("11-Experiments",),
    "排障": ("03-Debug",),
    "团队": ("06-Team",),
    "题目": ("08-Problems",),
    "面试": ("09-Interview",),
}


def _err(code: str, message: str, hint: str = "") -> dict[str, Any]:
    return {"code": code, "message": message, "hint": hint}


def _iso(ts: Any) -> str | None:
    if not isinstance(ts, (int, float)) or not ts:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes")


def cut(text: Any, limit: int = TEXT_CHARS) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@dataclass
class Plane:
    vault: Path
    db: Path
    search_fn: SearchFn | None = None  # tests swap the index for a stub
    clock: Callable[[], int] = field(default=lambda: int(time.time()))

    def search(self, query: str, top_k: int, filters: dict[str, str] | None,
               rerank: bool) -> Result:
        if self.search_fn is not None:
            return self.search_fn(query, top_k, filters, rerank)
        from kg.retrieval.service import get_searcher

        return get_searcher("hybrid", rerank, vault=self.vault).search(
            query, top_k=top_k, filters=filters)

    def connect(self) -> Any:
        from kg.plane.store import connect

        # Reading never creates the store, like the API.
        return connect(self.db if self.db.exists() else ":memory:")


# ── search_knowledge / get_entry ────────────────────────────────────────────────────────────────


def board_dirs(board: str | None) -> tuple[str, ...] | None:
    """Folders for a board name, a folder name (`03-Debug`) or an entry type (`debug`)."""
    from agents.utils import _TYPE_DIR_MAP, VAULT_ENTRY_DIRS

    if not board:
        return None
    key = board.strip()
    if key in BOARDS:
        return BOARDS[key]
    if key.lower() in _TYPE_DIR_MAP:
        return (_TYPE_DIR_MAP[key.lower()],)
    for name in VAULT_ENTRY_DIRS:
        if key.lower() in (name.lower(), name.lower().split("-", 1)[1]):
            return (name,)
    return ()


def _board_of(path: str) -> str:
    top = path.split("/", 1)[0]
    return next((name for name, dirs in BOARDS.items() if top in dirs), top)


def search_knowledge(plane: Plane, query: str, board: str | None = None, limit: int = 5,
                     rerank: bool = False) -> dict[str, Any]:
    from kg.plane.heal.index_status import search_warning
    from kg.retrieval.errors import RetrievalError

    limit = max(1, min(int(limit or 5), 20))
    out: dict[str, Any] = {"query": query, "board": board,
                           "mode": "hybrid+rerank" if rerank else "hybrid", "results": []}
    dirs = board_dirs(board)
    if dirs == ():
        out["error"] = _err("unknown_board", f"没有板块 {board}",
                            "可选：" + "、".join(BOARDS) + "，或目录名 / 条目类型")
        return out
    try:
        # A board filter is applied after fusion, so take a deeper pool first.
        result = plane.search(query, 50 if dirs else limit, None, rerank)
    except RetrievalError as exc:
        out["error"] = exc.to_dict()
        return out
    hits = [h for h in result.hits if not dirs or h.path.split("/", 1)[0] in dirs][:limit]
    out["results"] = [{
        "id": h.entry_id, "title": h.title, "path": h.path, "board": _board_of(h.path),
        "type": h.type, "domain": list(h.domain), "heading": h.heading, "snippet": h.snippet,
        "score": round(h.score, 4),
    } for h in hits]
    warnings = list(result.warnings)
    stale = search_warning(plane.vault)
    if stale:
        warnings.append(stale)
    out["warnings"] = warnings
    out["timings_ms"] = result.timings
    return out


def _find_entry_file(vault: Path, ref: str) -> Path | None:
    import frontmatter

    from agents.utils import VAULT_ENTRY_DIRS

    ref = ref.strip()
    if ref.endswith(".md") and "/" in ref:
        file = (vault / ref).resolve()
        root = vault.resolve()
        top = file.relative_to(root).parts[0] if root in file.parents else ""
        return file if top in VAULT_ENTRY_DIRS and file.is_file() else None
    if not ref or "/" in ref or ref.startswith("."):
        return None
    for name in VAULT_ENTRY_DIRS:
        direct = vault / name / f"{ref}.md"
        if direct.is_file():
            return direct
    for name in VAULT_ENTRY_DIRS:  # file named differently from its id: read frontmatter
        folder = vault / name
        for file in folder.rglob("*.md") if folder.is_dir() else ():
            try:
                if str(frontmatter.load(str(file)).metadata.get("id") or "") == ref:
                    return file
            except Exception:  # noqa: BLE001, S112 - a broken file is just not this entry
                continue
    return None


def get_entry(plane: Plane, id: str) -> dict[str, Any]:  # noqa: A002 - the tool's parameter
    import frontmatter

    file = _find_entry_file(plane.vault, id)
    if file is None:
        return {"id": id, "error": _err("entry_not_found", f"知识库里没有条目 {id}",
                                        "先用 search_knowledge 找到 id 或路径")}
    post = frontmatter.load(str(file))
    meta = _jsonable(dict(post.metadata))
    body = post.content
    return {
        "id": str(meta.get("id") or file.stem), "title": meta.get("title") or file.stem,
        "path": file.relative_to(plane.vault).as_posix(), "type": meta.get("type"),
        "status": meta.get("status"), "updated": meta.get("updated"), "frontmatter": meta,
        "body": body[:BODY_CHARS], "truncated": len(body) > BODY_CHARS, "chars": len(body),
    }


# ── project_status / recent_sessions ────────────────────────────────────────────────────────────


def _match_project(query: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    q = query.strip().lower().removeprefix("project:")
    if not q:
        return None

    def keys(p: dict[str, Any]) -> set[str]:
        kb = p.get("kb") or {}
        out = {str(p.get("name") or "").lower()}
        if kb.get("path"):
            stem = Path(kb["path"]).stem.lower()
            out |= {stem, stem.removeprefix("proj-")}
        if kb.get("title"):
            out.add(str(kb["title"]).lower())
        return out - {""}

    exact = [p for p in items if q in keys(p)]
    if exact:
        return exact[0]
    partial = [p for p in items if any(q in k for k in keys(p))]
    return partial[0] if len(partial) == 1 else None


def _session_view(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": s.get("id"), "source": s.get("source"), "project": s.get("project"),
        "state": s.get("state"), "branch": s.get("branch"),
        "started": _iso(s.get("started")) if isinstance(s.get("started"), (int, float))
        else s.get("started"),
        "last_active": _iso(s.get("last_active")), "turns": s.get("turns"),
        "last_user": cut(s.get("last_user"), 300),
        "conclusion": cut(s.get("last_agent_tail") or s.get("last_agent")),
        "waiting_reason": s.get("waiting_reason") or None, "resume": s.get("resume"),
    }


def _store_note(plane: Plane, fresh: dict[str, Any]) -> str | None:
    if not plane.db.exists():
        return "状态库还没建（先运行 kg plane collect sessions），下面只有知识库项目页的内容"
    if fresh.get("collected_at") is None:
        return "状态库里还没有 sessions 采集数据（运行 kg plane collect sessions）"
    if fresh.get("stale"):
        return f"进行中看板超过两个周期没采集（上次 {_iso(fresh['collected_at'])}），可能过时"
    return None


def project_status(plane: Plane, project: str) -> dict[str, Any]:
    from agents.api_routes.sessions import get_projects, get_sessions

    board = get_projects(plane.db)
    items = board["items"]
    note = _store_note(plane, board)
    found = _match_project(project, items)
    if found is None:
        from kg.plane.collectors.sessions import match_page, read_project_pages

        page = match_page(project.strip(), read_project_pages(plane.vault))
        out: dict[str, Any] = {"project": project, "known_projects": [p["name"] for p in items]}
        if page is not None:
            out["kb"] = {"path": page.path, "title": page.title, "status": page.status,
                         "state": page.state, "as_of": page.as_of, "updated": page.updated}
            out["warning"] = note or "看板里没有这个项目（最近没有会话或工作区），只有知识库项目页"
        else:
            out["error"] = _err("project_not_found", f"看板和知识库项目页里都没有 {project}",
                                "换一个名字，可选的见 known_projects")
            if note:
                out["warning"] = note
        return out
    name = found["name"]
    sessions = get_sessions(plane.db, project=name, state=None, limit=2000)["items"]
    live = [s for s in sessions if s.get("state") in ("working", "waiting")]
    conclusion = found.get("last_conclusion") or None
    orca = found.get("orca")
    return {
        "project": name, "id": found.get("id"), "stage": found.get("stage"),
        "stage_label": found.get("stage_label"), "reason": found.get("reason"),
        "last_action": _iso(found.get("last_action")),
        "kb": found.get("kb"),
        "last_conclusion": {**conclusion, "text": cut(conclusion.get("text")),
                            "ts": _iso(conclusion.get("ts"))} if conclusion else None,
        "sessions": found.get("sessions"),
        "active_sessions": [_session_view(s) for s in live[:5]],
        "latest_sessions": [_session_view(s) for s in sessions[:3]],
        "repos": [{k: r.get(k) for k in ("name", "branch", "upstream", "ahead", "behind",
                                         "dirty", "changed", "untracked", "conflicts", "github")}
                  | {"last_commit": _iso(r.get("last_commit"))}
                  for r in found.get("repos") or []],
        "dirty": found.get("dirty"),
        "open_prs": found.get("open_prs"),  # None: no GitHub snapshot covers this repo
        "prs": found.get("prs") or [],
        "orca": {"live": orca.get("live"), "unread": orca.get("unread"),
                 "worktrees": len(orca.get("worktrees") or [])} if orca else None,
        "collected_at": _iso(board.get("collected_at")), "stale": board.get("stale"),
        **({"warning": note} if note else {}),
    }


def recent_sessions(plane: Plane, project: str | None = None, hours: float = 24,
                    limit: int = 20) -> dict[str, Any]:
    from agents.api_routes.sessions import get_projects, get_sessions

    hours = max(0.1, min(float(hours or 24), 24 * 7))
    limit = max(1, min(int(limit or 20), 100))
    name = None
    out: dict[str, Any] = {"project": project, "hours": hours}
    if project:
        found = _match_project(project, get_projects(plane.db)["items"])
        if found is None:
            data = get_sessions(plane.db, project=None, state=None, limit=2000)
            out["error"] = _err("project_not_found", f"看板里没有项目 {project}",
                                "不带 project 查全部，或换个名字")
            out["known_projects"] = sorted({s.get("project") for s in data["items"]} - {None})
            return out
        name = found["name"]
    data = get_sessions(plane.db, project=name, state=None, limit=2000)
    since = plane.clock() - hours * 3600
    items = [s for s in data["items"] if (s.get("last_active") or 0) >= since]
    note = _store_note(plane, data)
    out.update({"project": name or project, "count": len(items),
                "sessions": [_session_view(s) for s in items[:limit]],
                "collected_at": _iso(data.get("collected_at")), "stale": data.get("stale")})
    if note:
        out["warning"] = note
    return out


# ── plane_health ────────────────────────────────────────────────────────────────────────────────


def _index_health(vault: Path) -> dict[str, Any]:
    from kg.plane.heal.index_status import read_index_status
    from kg.retrieval.errors import RetrievalError
    from kg.retrieval.index import Index

    index = Index(vault)
    status = index.status()
    marked = read_index_status(vault)
    out: dict[str, Any] = {
        "built": index.manifest_path.exists(), "ok": status.get("ok"),
        "last_success": status.get("last_success"), "entries": status.get("entries"),
        "error": status.get("error"),
        "marked_stale": marked.stale, "stale_reason": marked.reason or None,
    }
    try:
        pend = index.pending()
        out["pending"] = {"added": len(pend.added), "modified": len(pend.modified),
                          "deleted": len(pend.deleted)}
    except RetrievalError as exc:
        out["pending"] = None
        out["error"] = out["error"] or exc.to_dict()
    return out


def plane_health(plane: Plane) -> dict[str, Any]:
    from agents.api_routes.overview import plane_health as pills
    from agents.api_routes.sessions import get_projects
    from kg.plane.store import list_incidents, list_resources

    now = plane.clock()
    groups = [p for p in pills(plane.db, plane.vault, now) if p["id"] != "index"]
    conn = plane.connect()
    try:
        incidents = list_incidents(conn, open_only=True, limit=100)
        egress = list_resources(conn, kind="egress")
    finally:
        conn.close()
    board = get_projects(plane.db)
    out: dict[str, Any] = {
        "now": _iso(now),
        "groups": [{k: p.get(k) for k in ("id", "label", "status", "text", "count", "down",
                                          "availability", "stale", "reason")}
                   | {"last": _iso(p.get("last"))} for p in groups],
        "egress": [{"id": r["id"], "name": r["name"], "status": r["status"],
                    "since": _iso(r["status_ts"]), "latency_ms": r["value"]} for r in egress],
        "open_incidents": [{
            "id": i["id"], "resource": i["resource_id"], "rule": i["rule"],
            "severity": i["severity"], "summary": i["summary"],
            "opened_at": _iso(i["opened_at"]),
            "minutes_open": (now - i["opened_at"]) // 60,
        } for i in incidents],
        "waiting_sessions": len(board["attention"]["waiting"]),
        "stalled_projects": [p["name"] for p in board["attention"]["stalled"]],
        "index": _index_health(plane.vault),
    }
    if not plane.db.exists():
        out["warning"] = "状态库还没建：出口、服务、事故都没有数据（先跑 kg plane collect）"
    return out


# ── find_artifact ───────────────────────────────────────────────────────────────────────────────


def _terms(text: str) -> list[str]:
    from kg.retrieval.text import tokenize

    return list(dict.fromkeys(t for t in tokenize(text) if t.strip()))


def _snippet(text: str, terms: list[str], width: int = 160) -> str:
    low = text.lower()
    pos = min((p for p in (low.find(t) for t in terms) if p >= 0), default=-1)
    if pos < 0:
        return cut(text, width)
    start = max(0, pos - width // 3)
    return ("…" if start else "") + text[start:start + width].replace("\n", " ").strip() + "…"


def find_artifact(plane: Plane, query: str, limit: int = 10) -> dict[str, Any]:
    from kg.plane.artifact_docs import doc_from_file
    from kg.plane.sensitive import Scanner
    from kg.plane.store import list_resources

    limit = max(1, min(int(limit or 10), 50))
    terms = _terms(query)
    out: dict[str, Any] = {"query": query, "results": []}
    if not terms:
        out["error"] = _err("empty_query", "查询里没有可用的词")
        return out
    conn = plane.connect()
    try:
        rows = list_resources(conn, kind="artifact")
    finally:
        conn.close()
    if not plane.db.exists() or not rows:
        out["warning"] = "状态库里还没有工件（运行 kg plane collect artifacts）"
    scanner = Scanner()
    now = plane.clock()
    scored = []
    phrase = query.strip().lower()
    for r in rows:
        attrs = r["attrs"] or {}
        head = " ".join(str(attrs.get(k) or "") for k in ("title", "origin", "project", "url"))
        head = f"{r['name']} {head}".lower()
        body = ""
        if attrs.get("local_file"):
            doc = doc_from_file(r["id"], Path(attrs["local_file"]), attrs=attrs, scanner=scanner,
                                max_bytes=ARTIFACT_TEXT_BYTES)
            body = doc.text if doc else ""
        low = body.lower()
        score = sum(3 for t in terms if t in head) + sum(1 for t in terms if t in low)
        if phrase and phrase in head:
            score += 5
        if score:
            scored.append((score, attrs.get("updated_at") or r["first_seen"] or 0, r, body))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    for score, _, r, body in scored[:limit]:
        a = r["attrs"] or {}
        expires = a.get("expires_at")
        out["results"].append({
            "id": r["id"], "title": a.get("title") or r["name"], "origin": a.get("origin"),
            "visibility": a.get("visibility"), "url": a.get("url"),
            "local_file": a.get("local_file"), "project": a.get("project"),
            "updated_at": _iso(a.get("updated_at")), "expires_at": _iso(expires),
            "days_left": round((expires - now) / DAY, 1) if expires else None,
            "expired": bool(expires and expires <= now), "status": r["status"],
            "snippet": _snippet(body, terms) if body else "", "score": score,
        })
    out["count"] = len(scored)
    return out


# ── similar_incidents ───────────────────────────────────────────────────────────────────────────


def similar_incidents(plane: Plane, text: str, limit: int = 5, *,
                      with_entries: bool = True) -> dict[str, Any]:
    """Past incidents ranked by term overlap (idf-weighted), plus debug entries from the index.

    `with_entries=False` skips the index (and its model load), for the session-start brief.
    """
    import json

    from kg.plane.heal import audit
    from kg.plane.heal.retro import find_entry
    from kg.plane.store import get_resource, list_incidents
    from kg.retrieval.errors import RetrievalError

    limit = max(1, min(int(limit or 5), 20))
    terms = _terms(text)
    out: dict[str, Any] = {"text": text, "incidents": [], "retro_entries": []}
    if not terms:
        out["error"] = _err("empty_query", "描述里没有可用的词")
        return out
    conn = plane.connect()
    try:
        rows = list_incidents(conn, limit=2000)
        names = {}
        for r in rows:
            if r["resource_id"] not in names:
                res = get_resource(conn, r["resource_id"])
                names[r["resource_id"]] = res["name"] if res else ""
    finally:
        conn.close()
    docs = []
    for r in rows:
        blob = " ".join([r["rule"], r["rule"].replace(".", " "), r["resource_id"],
                         names.get(r["resource_id"], ""), r["summary"] or "",
                         json.dumps(r.get("evidence") or {}, ensure_ascii=False)[:2000]])
        docs.append((r, Counter(_terms(blob))))
    n = len(docs) or 1
    df = Counter(t for _, c in docs for t in c)
    scored = []
    for r, c in docs:
        score = sum(math.log(1 + n / df[t]) for t in terms if t in c)
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], -x[1]["opened_at"]))
    trail = audit.read(plane.vault) if scored else []
    for score, r in scored[:limit]:
        entry = None
        if r.get("entry_id"):
            file = find_entry(plane.vault, r["entry_id"])
            entry = {"id": r["entry_id"],
                     "path": file.relative_to(plane.vault).as_posix() if file else None}
        actions = [{"action": e.action, "mode": e.mode, "result": e.result}
                   for e in trail if e.incident == r["id"]]
        closed = r.get("closed_at")
        out["incidents"].append({
            "id": r["id"], "resource": r["resource_id"], "rule": r["rule"],
            "severity": r["severity"], "summary": r["summary"],
            "opened_at": _iso(r["opened_at"]), "closed_at": _iso(closed),
            "minutes": (closed - r["opened_at"]) // 60 if closed else None,
            "open": closed is None, "retro": entry, "actions": actions[:10],
            "score": round(score, 3),
        })
    if not with_entries:
        out.pop("retro_entries")
        return out
    try:
        result = plane.search(text, 5, {"type": "debug"}, False)
        out["retro_entries"] = [{"id": h.entry_id, "title": h.title, "path": h.path,
                                 "snippet": h.snippet, "score": round(h.score, 4)}
                                for h in result.hits]
    except RetrievalError as exc:
        out["search_error"] = exc.to_dict()
    if not plane.db.exists():
        out["warning"] = "状态库还没建，没有历史事故；只查了知识库里的排障条目"
    return out
