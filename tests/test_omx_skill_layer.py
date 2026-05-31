"""Tests for the project-level OMX skill external capability layer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from integrations.omx import kg_skill


def test_skill_files_exist_with_required_frontmatter() -> None:
    skill_files = [
        Path(".codex/skills/kg-load/SKILL.md"),
        Path(".codex/skills/kg-ask/SKILL.md"),
        Path(".codex/skills/kg-review/SKILL.md"),
        Path(".codex/skills/kg-capture/SKILL.md"),
    ]

    for skill_file in skill_files:
        content = skill_file.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "\nname: " in content
        assert "\ndescription: " in content


def test_run_load_prints_context_summary(capsys) -> None:
    results = [
        {
            "id": "ke-test",
            "title": "Test Entry",
            "type": "principle",
            "domain": "ai-agent",
            "score": 0.91,
            "file_path": "",
        }
    ]

    with patch("integrations.omx.kg_skill.search", return_value=results) as mock_search:
        exit_code = kg_skill.run_load(topic="mcp", top_k=3, domain=None)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "KG Context: mcp" in out
    assert "ke-test" in out
    mock_search.assert_called_once_with(query="mcp", filters=None, top_k=3)


def test_run_load_uses_domain_filter_for_semantic_search(capsys) -> None:
    with patch("integrations.omx.kg_skill.search", return_value=[]) as mock_search:
        exit_code = kg_skill.run_load(topic="OpenClaw Web BOM", top_k=10, domain="ai-agent")

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "未找到相关知识条目" in out
    mock_search.assert_called_once_with(
        query="OpenClaw Web BOM",
        filters={"domain": "ai-agent"},
        top_k=10,
    )


def test_run_load_prints_excerpt_and_path_from_semantic_hit(tmp_path, capsys) -> None:
    entry_path = tmp_path / "entry.md"
    entry_path.write_text(
        "---\nid: ke-test\ntitle: Test Entry\n---\n\n"
        "# Test Entry\n\n"
        "This semantic hit explains the OpenClaw Web BOM bridge.\n\n"
        "It should be available to the context briefing.\n",
        encoding="utf-8",
    )
    results = [
        {
            "id": "ke-test",
            "title": "Test Entry",
            "type": "architecture",
            "domain": "ai-agent",
            "score": 0.93,
            "file_path": str(entry_path),
        }
    ]

    with patch("integrations.omx.kg_skill.search", return_value=results):
        exit_code = kg_skill.run_load(topic="OpenClaw Web BOM", top_k=10, domain=None)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "This semantic hit explains the OpenClaw Web BOM bridge" in out
    assert str(entry_path) in out


def test_run_review_groups_results_by_type(capsys) -> None:
    results = [
        {
            "id": "ke-a",
            "title": "Pattern A",
            "type": "pattern",
            "domain": "ai-agent",
            "score": 0.8,
            "file_path": "",
        },
        {
            "id": "ke-b",
            "title": "Debug B",
            "type": "debug",
            "domain": "ai-agent",
            "score": 0.7,
            "file_path": "",
        },
    ]

    with patch("integrations.omx.kg_skill.search", return_value=results):
        exit_code = kg_skill.run_review(topic="agent", top_k=8, domain=None)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "## pattern" in out
    assert "## debug" in out


def test_run_ask_forces_read_only_mode() -> None:
    with patch("integrations.omx.kg_skill.ask") as mock_ask:
        exit_code = kg_skill.run_ask(question="what is mcp?", top_k=4, domain="ai-agent")

    assert exit_code == 0
    mock_ask.assert_called_once_with(
        question="what is mcp?",
        top_k=4,
        domain="ai-agent",
        use_graph=True,
        enable_fallback=False,
    )


def test_run_capture_emits_proposal_only_output(capsys) -> None:
    results = [
        {
            "id": "ke-related",
            "title": "Related Entry",
            "type": "pattern",
            "domain": "ai-agent",
            "score": 0.82,
            "file_path": "",
        }
    ]

    with patch("integrations.omx.kg_skill.search", return_value=results):
        exit_code = kg_skill.run_capture(
            subject="MCP tool routing",
            notes="Prefer read-only tools first.",
            top_k=5,
            domain=None,
            entry_type="research",
            depth="intermediate",
        )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "mode: proposal-only" in out
    assert "MCP tool routing" in out
    assert "ke-related" in out
