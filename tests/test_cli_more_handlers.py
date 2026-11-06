"""Regression tests for additional extracted CLI command handlers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from agents.cli import cli


def test_review_command_uses_new_review_service_surface() -> None:
    runner = CliRunner()

    with patch("kg.application.services.review_service.print_review_summary") as mock_summary:
        result = runner.invoke(cli, ["review", "--domain", "ai-agent"])

    assert result.exit_code == 0
    mock_summary.assert_called_once_with(domain_filter="ai-agent")


def test_stats_command_delegates_to_new_handler() -> None:
    runner = CliRunner()

    with patch("kg.interfaces.cli.commands.stats.run_stats_command") as mock_stats:
        result = runner.invoke(cli, ["stats"])

    assert result.exit_code == 0
    assert mock_stats.call_count == 1


def test_sync_command_delegates_to_new_handler_with_flags() -> None:
    runner = CliRunner()

    with patch("kg.interfaces.cli.commands.sync.run_sync_command") as mock_sync:
        result = runner.invoke(cli, ["sync", "--full"])

    assert result.exit_code == 0
    mock_sync.assert_called_once()
    assert mock_sync.call_args.kwargs["full"] is True


def test_review_report_prints_generated_text_when_no_output_path() -> None:
    runner = CliRunner()

    with patch(
        "kg.application.services.review_service.generate_review_report",
        return_value="# review report",
    ) as mock_report:
        result = runner.invoke(cli, ["review", "--report"])

    assert result.exit_code == 0
    assert "# review report" in result.output
    mock_report.assert_called_once_with(domain_filter=None, output_path=None)


def test_review_report_passes_output_path_through() -> None:
    runner = CliRunner()

    with patch(
        "kg.application.services.review_service.generate_review_report",
        return_value="ignored",
    ) as mock_report:
        result = runner.invoke(cli, ["review", "--output", "report.md"])

    assert result.exit_code == 0
    mock_report.assert_called_once_with(
        domain_filter=None,
        output_path=Path("report.md"),
    )
