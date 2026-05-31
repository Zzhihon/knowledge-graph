"""Regression tests for entry preview lookup behavior."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

from fastapi.testclient import TestClient

from agents.api import app

if TYPE_CHECKING:
    from pathlib import Path


def _write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_entry_route_finds_frontmatter_id_even_when_filename_is_human_friendly(
    tmp_path: Path,
) -> None:
    interview_dir = tmp_path / "09-Interview"
    _write_markdown(
        interview_dir / "高频-工作流并行与扩展性.md",
        """---
id: iq-20260406-hfq-07-workflow-engine-details
title: 高频 07：并行网关、Token、Hook、Calculator 怎么讲？
type: interview
category: project-deep-dive
status: active
created: '2026-04-06'
updated: '2026-04-06'
review_date: '2026-04-06'
tags:
  - mock-simulation
  - personalized-high-frequency
---

# Test Entry

hello
""",
    )

    client = TestClient(app)
    config = SimpleNamespace(vault_path=tmp_path)

    with patch("agents.config.load_config", return_value=config):
        response = client.get("/api/entries/iq-20260406-hfq-07-workflow-engine-details")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "iq-20260406-hfq-07-workflow-engine-details"
    assert body["title"] == "高频 07：并行网关、Token、Hook、Calculator 怎么讲？"
    assert body["relative_path"] == "09-Interview/高频-工作流并行与扩展性.md"
