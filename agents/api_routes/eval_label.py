"""/api/eval/*: the retrieval eval set's review page (知识库 → 评测).

The draft lives in `.kg/eval/draft.json`: questions mined from sessions, each with the entries a
search should find. The page confirms or changes them; publishing writes `.kg/eval/retrieval.jsonl`,
which `kg.eval.retrieval` scores against. Writes only from this page: see agents/local_guard.py.
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003 - FastAPI reads the annotation at runtime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agents.api_routes import library
from agents.local_guard import require_local, require_same_origin_json

router = APIRouter(prefix="/eval", tags=["eval"], dependencies=[Depends(require_local)])
STATES = ("draft", "ok", "skip")


def get_vault() -> Path:
    return library._vault()


Vault = Annotated[Path, Depends(get_vault)]


def _eval_dir(vault: Path) -> Path:
    return vault / ".kg" / "eval"


@router.get("/draft")
def draft(vault: Vault) -> Any:
    """The draft, with title, board and path for every entry it mentions."""
    file = _eval_dir(vault) / "draft.json"
    if not file.is_file():
        return JSONResponse({"error": "还没有评测草稿：先跑 kg eval 挖候选问题"}, status_code=404)
    items = json.loads(file.read_text(encoding="utf-8"))
    wanted = {eid for it in items for eid in [*it.get("expected", []), *it.get("candidates", [])]}
    entries: dict[str, dict[str, str]] = {}
    for md in library._files(vault):
        card = library._card(vault, md)
        for key in (md.stem, card["id"]):
            if key in wanted and key not in entries:
                entries[key] = {
                    "title": card["title"],
                    "board": card["board"],
                    "path": card["path"],
                }
    return {"items": items, "entries": entries}


class Items(BaseModel):
    items: list[dict[str, Any]] = Field(..., max_length=500)


def _clean(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = []
    for it in items:
        expected = [str(x) for x in it.get("expected") or []][:10]
        state = it.get("state") if it.get("state") in STATES else "draft"
        clean.append(
            {
                **{k: it.get(k) for k in ("n", "q", "source", "project", "unsure")},
                "expected": expected,
                "candidates": [str(x) for x in it.get("candidates") or []][:20],
                "state": state,
                "none": bool(it.get("none")) and not expected,
            }
        )
    return clean


def _save(vault: Path, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    folder = _eval_dir(vault)
    folder.mkdir(parents=True, exist_ok=True)
    clean = _clean(items)
    tmp = folder / ".draft.json.tmp"
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(folder / "draft.json")
    return clean


@router.post("/draft", dependencies=[Depends(require_same_origin_json)])
def save_draft(body: Items, vault: Vault) -> Any:
    return {"saved": len(_save(vault, body.items))}


@router.post("/publish", dependencies=[Depends(require_same_origin_json)])
def publish(body: Items, vault: Vault) -> Any:
    """Save the draft and write the eval set: every question not skipped."""
    clean = _save(vault, body.items)
    keep = [c for c in clean if c["state"] != "skip"]
    lines = [
        json.dumps(
            {"q": c["q"], "expected": c["expected"], "source": c["source"]}, ensure_ascii=False
        )
        for c in keep
    ]
    out = _eval_dir(vault) / "retrieval.jsonl"
    tmp = out.with_name(".retrieval.jsonl.tmp")
    tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    tmp.replace(out)
    return {
        "saved": len(clean),
        "published": len(keep),
        "labelled": sum(1 for c in keep if c["expected"]),
    }
