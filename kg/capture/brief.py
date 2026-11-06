"""Gather what the daily brief is written from, without calling any model.

Writes `<vault>/.kg/brief/<date>/input.json`: capture batches processed since the
last brief, project pages and experiments touched in that window, extra local
files (such as the GitHub digest), the control plane's incidents, retro entries and
weekly report (`plane`, when there is a state store), and AIHOT's daily report plus
selected items.
AIHOT is public and read-only; a failed fetch is recorded, not fatal.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import frontmatter
import httpx

from kg.capture.scan import RESULT_FILE, TZ, CaptureConfig

AIHOT = "https://aihot.news/api/v1"
# AIHOT answers 403 to non-browser user agents.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
_MAX_ITEMS = 100
_EXTRA_LIMIT = 20_000


def _item(raw: dict[str, Any]) -> dict[str, Any]:
    links = raw.get("links") or {}
    return {
        "title": raw.get("title", ""),
        "summary": raw.get("summary", ""),
        "reason": raw.get("reason", ""),
        "category": raw.get("category", ""),
        "source": (raw.get("source") or {}).get("name", ""),
        "url": links.get("original") or links.get("aihot", ""),
        "publishedAt": raw.get("publishedAt", ""),
        "score": raw.get("score"),
    }


def _fetch_aihot(since: datetime) -> dict[str, Any]:
    """AIHOT's latest daily report and the selected items published since `since`."""
    out: dict[str, Any] = {}
    # The items API only offers a 24h or 7d window; anything older is cut off at 7 days.
    window = "24h" if datetime.now(TZ) - since <= timedelta(hours=24) else "7d"
    with httpx.Client(timeout=20, follow_redirects=True,
                      headers={"User-Agent": _BROWSER_UA}) as client:
        try:
            out["daily"] = client.get(f"{AIHOT}/dailies/latest").raise_for_status().json()["report"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            out["daily_error"] = str(exc)[:300]
        items: list[dict[str, Any]] = []
        params: dict[str, Any] = {"mode": "selected", "limit": 50, "window": window}
        try:
            while len(items) < _MAX_ITEMS:
                page = client.get(f"{AIHOT}/items", params=params).raise_for_status().json()
                fresh = [i for i in page.get("items", []) if _published(i, since)]
                items.extend(_item(i) for i in fresh)
                paging = page.get("page") or {}
                if len(fresh) < len(page.get("items", [])) or not paging.get("hasMore"):
                    break
                params["cursor"] = paging["nextCursor"]
                time.sleep(0.2)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            out["items_error"] = str(exc)[:300]
        out["items"] = items[:_MAX_ITEMS]
    return out


def _published(raw: dict[str, Any], since: datetime) -> bool:
    """Whether an item came out after `since`; items without a usable date are kept."""
    try:
        moment = datetime.fromisoformat(str(raw.get("publishedAt", "")).replace("Z", "+00:00"))
    except ValueError:
        return True
    return moment >= since


def _touched(folder: Path, since: float, vault: Path) -> list[dict[str, Any]]:
    if not folder.is_dir():
        return []
    found = []
    for file in sorted(folder.rglob("*.md")):
        if file.stat().st_mtime < since:
            continue
        try:
            meta = frontmatter.load(str(file)).metadata
        except Exception:
            meta = {}
        found.append({
            "path": str(file.relative_to(vault)),
            "id": meta.get("id", ""),
            "title": meta.get("title", file.stem),
            "status": meta.get("status", ""),
        })
    return found


def _plane_block(vault: Path, since: float) -> dict[str, Any] | None:
    """Incidents, retro entries and the weekly report from the control plane (#9), if any.

    Optional: no state store means no block, and a failure is recorded, never fatal.
    """
    try:
        from kg.plane.heal.config import load_config as load_heal_config
        from kg.plane.heal.report import brief_block

        return brief_block(vault, load_heal_config(vault), since=int(since),
                           now=int(time.time()))
    except Exception as exc:  # the brief must not fail because of the control plane
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def collect_brief(vault: Path, config: CaptureConfig, *, fetch: bool = True) -> tuple[str, Path]:
    """Write today's brief input; returns its id and file."""
    raw = config.brief
    root = vault / ".kg" / "brief"
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TZ)
    last_file = root / "last.json"
    since = now - timedelta(days=1)
    if last_file.exists():
        since = datetime.fromisoformat(json.loads(last_file.read_text())["at"])
    cutoff = since.timestamp()

    batches = []
    if config.inbox.is_dir():
        for folder in sorted(config.inbox.iterdir()):
            result = folder / RESULT_FILE
            if result.exists() and result.stat().st_mtime >= cutoff:
                batches.append(str(result.relative_to(vault)))

    day = now.date().isoformat()
    # Copied in rather than referenced: the brief agent may only read inside the vault.
    extra = []
    for pattern in raw.get("extra", []):
        file = Path(str(pattern).replace("{date}", day)).expanduser()
        if file.exists():
            text = file.read_text(encoding="utf-8", errors="replace")
            extra.append({"path": str(file), "content": text[:_EXTRA_LIMIT]})

    content: dict[str, Any] = {
        "date": day,
        "since": since.isoformat(timespec="seconds"),
        "batches": batches,
        "projects": _touched(vault / "07-Projects", cutoff, vault),
        "experiments": _touched(vault / "11-Experiments", 0, vault),
        "experiences": _touched(vault / "10-Experience", cutoff, vault),
        "extra": extra,
    }
    if raw.get("plane", True):
        plane = _plane_block(vault, cutoff)
        if plane is not None:
            content["plane"] = plane
    if fetch and raw.get("aihot", True):
        content["aihot"] = _fetch_aihot(since)
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode())
    brief_id = f"{day}-{digest.hexdigest()[:10]}"
    folder = root / day
    folder.mkdir(exist_ok=True)
    file = folder / "input.json"
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps({"id": brief_id, **content}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(file)
    last_file.write_text(json.dumps({"at": now.isoformat(), "id": brief_id}))
    return brief_id, file
