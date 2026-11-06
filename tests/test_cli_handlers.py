"""Regression tests for extracted CLI command handlers."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from agents.cli import cli


def test_query_command_still_delegates_to_search_and_renderer() -> None:
    runner = CliRunner()

    with (
        patch(
            "kg.application.services.search_service.search",
            return_value=[{"id": "ke-test"}],
        ) as mock_search,
        patch("kg.application.services.search_service.print_results") as mock_print_results,
    ):
        result = runner.invoke(cli, ["query", "test question"])

    assert result.exit_code == 0
    mock_search.assert_called_once_with(
        query="test question",
        filters=None,
        top_k=5,
    )
    mock_print_results.assert_called_once()


def test_ask_command_still_forwards_flags_to_service() -> None:
    runner = CliRunner()

    with patch("kg.application.services.ask_service.ask") as mock_ask:
        result = runner.invoke(
            cli,
            ["ask", "what is mcp", "--top-k", "3", "--domain", "ai-agent", "--no-graph"],
        )

    assert result.exit_code == 0
    mock_ask.assert_called_once_with(
        question="what is mcp",
        top_k=3,
        domain="ai-agent",
        use_graph=False,
        enable_fallback=True,
    )
