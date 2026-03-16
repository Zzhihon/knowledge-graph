"""Export endpoint: generate formatted documents from knowledge entries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["export"])


class ExportRequest(BaseModel):
    format: str  # "blog" | "guide" | "onboarding"
    domain: str | None = None


@router.post("/export")
def post_export(req: ExportRequest) -> dict[str, Any]:
    """Export knowledge entries as formatted markdown."""
    from agents.config import load_config
    from agents.export_entries import (
        export_blog,
        export_onboarding,
        export_study_guide,
        write_export,
    )
    from agents.utils import load_entries

    config = load_config()
    entries = load_entries(config.vault_path)

    if not entries:
        return {"error": "Knowledge base is empty"}

    if req.format == "blog":
        content = export_blog(entries, domain=req.domain)
    elif req.format == "guide":
        content = export_study_guide(entries, domain=req.domain)
    elif req.format == "onboarding":
        content = export_onboarding(entries, team=req.domain)
    else:
        return {"error": f"Unknown format: {req.format}"}

    result_path = write_export(content, format_name=req.format)

    return {
        "content": content,
        "file_path": str(result_path),
    }
