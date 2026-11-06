"""Artifact center endpoints: the artifacts the `artifacts` collector recorded, and a preview.

- `GET /api/artifacts`          every artifact with its attrs, latest signal (visibility,
                                sensitive hits, expiry) and open incidents.
- `GET /api/artifacts/preview`  the local file of one artifact (`id=<resource id>`), served
                                only when it sits inside a registered folder (`local:` and
                                `preview_roots:` in `.kg/plane/artifacts.yaml`), after symlinks
                                are resolved. The page is sent with `Content-Security-Policy:
                                sandbox`, so even opened outside the iframe it runs no script.

Both read the state store at `plane_db_path` and the config through `artifacts_config`,
so tests swap them with `app.dependency_overrides`.
"""

from __future__ import annotations

import sqlite3  # noqa: TC003 - FastAPI resolves the annotations at runtime
from collections.abc import Iterator  # noqa: TC003
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from agents.api_routes.plane import plane_db_path
from kg.plane.collectors.artifacts import ArtifactsConfig, load_artifacts_config
from kg.plane.store import connect, get_resource, list_incidents, list_resources, list_signals

router = APIRouter(prefix="/artifacts", tags=["artifacts"])

PREVIEW_TYPES = {".html": "text/html", ".htm": "text/html", ".md": "text/plain",
                 ".markdown": "text/plain", ".txt": "text/plain"}
PREVIEW_HEADERS = {
    "Content-Security-Policy": "sandbox; default-src 'none'; img-src data: blob:; "
                               "style-src 'unsafe-inline'; font-src data:",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def artifacts_config() -> ArtifactsConfig:
    from kg.infrastructure.config.loader import load_config

    return load_artifacts_config(load_config().vault_path)


@contextmanager
def _open(path: Path) -> Iterator[sqlite3.Connection]:
    # Opened and closed inside the handler's own thread: a generator dependency may run its
    # teardown on another threadpool thread, which sqlite3 refuses.
    conn = connect(path if path.exists() else ":memory:")
    try:
        yield conn
    finally:
        conn.close()


DbPath = Annotated[Path, Depends(plane_db_path)]
Config = Annotated[ArtifactsConfig, Depends(artifacts_config)]


def allowed(path: Path, roots: tuple[Path, ...]) -> Path | None:
    """The resolved file when it is a regular file inside one of `roots`, else None."""
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not real.is_file():
        return None
    for root in roots:
        if real == root or root in real.parents:
            return real
    return None


@router.get("")
def get_artifacts(db: DbPath, cfg: Config,
                  gone: bool = Query(False, description="也列出已消失的工件")) -> dict[str, Any]:
    """Artifacts with their latest signal detail and open incidents, newest first."""
    with _open(db) as conn:
        return _listing(conn, cfg, gone)


def _listing(conn: sqlite3.Connection, cfg: ArtifactsConfig, gone: bool) -> dict[str, Any]:
    incidents: dict[str, list[dict[str, Any]]] = {}
    for inc in list_incidents(conn, open_only=True, limit=2000):
        if inc["resource_id"].startswith("artifact:"):
            incidents.setdefault(inc["resource_id"], []).append(
                {k: inc[k] for k in ("id", "rule", "severity", "summary", "opened_at")})
    roots = cfg.roots
    items = []
    for item in list_resources(conn, kind="artifact", include_gone=gone):
        last = list_signals(conn, item["id"], limit=1)
        detail = (last[0].detail or {}) if last else {}
        attrs = item["attrs"]
        file = attrs.get("local_file")
        items.append({
            "id": item["id"], "name": item["name"], "attrs": attrs,
            "status": item["status"], "status_ts": item["status_ts"],
            "first_seen": item["first_seen"], "last_seen": item["last_seen"],
            "gone_at": item["gone_at"],
            "sensitive": detail.get("sensitive"), "hits": detail.get("hits") or [],
            "previewable": bool(file) and allowed(Path(file), roots) is not None,
            "incidents": incidents.get(item["id"], []),
        })
    items.sort(key=lambda i: i["attrs"].get("updated_at") or i["first_seen"] or 0, reverse=True)
    claude = cfg.claude_import
    return {"items": items, "count": len(items), "roots": [str(r) for r in roots],
            "claude_import": {"exists": bool(claude and claude.exists())}}


@router.get("/preview")
def preview(db: DbPath, cfg: Config,
            id: str = Query(..., description="工件的资源 id")) -> FileResponse:  # noqa: A002
    """Serve an artifact's local file, only from inside the registered folders."""
    with _open(db) as conn:
        item = get_resource(conn, id)
    if item is None or item["kind"] != "artifact":
        raise HTTPException(status_code=404, detail=f"没有工件 {id}")
    file = (item["attrs"] or {}).get("local_file")
    if not file:
        raise HTTPException(status_code=404, detail="这个工件没有本地文件")
    real = allowed(Path(file), cfg.roots)
    if real is None:
        raise HTTPException(status_code=403, detail="本地文件不在登记目录里，不提供预览")
    media = PREVIEW_TYPES.get(real.suffix.lower())
    if media is None:
        raise HTTPException(status_code=415, detail=f"不预览 {real.suffix or '无后缀'} 文件")
    return FileResponse(real, media_type=f"{media}; charset=utf-8", headers=PREVIEW_HEADERS)
