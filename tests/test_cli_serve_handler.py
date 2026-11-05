"""Regression tests for the extracted serve CLI handler."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from agents.cli import cli


def test_serve_command_delegates_to_new_handler() -> None:
    runner = CliRunner()

    with patch("kg.interfaces.cli.commands.serve.run_serve_command") as mock_serve:
        result = runner.invoke(cli, ["serve", "--port", "9999", "--dev"])

    assert result.exit_code == 0
    mock_serve.assert_called_once()
    assert mock_serve.call_args.kwargs["port"] == 9999
    assert mock_serve.call_args.kwargs["dev"] is True


def test_serve_handler_dev_mode_errors_when_web_project_missing(tmp_path) -> None:
    from kg.interfaces.cli.commands.serve import run_serve_command

    console = Console(record=True)
    config = SimpleNamespace(root_path=tmp_path)

    with (
        patch("kg.interfaces.cli.commands.serve.load_config", return_value=config),
        pytest.raises(SystemExit),
    ):
        run_serve_command(port=8765, dev=True, console=console)

    assert "前端项目未找到" in console.export_text()


def test_serve_handler_production_mode_runs_uvicorn(tmp_path) -> None:
    from kg.interfaces.cli.commands.serve import run_serve_command

    console = Console(record=True)
    config = SimpleNamespace(root_path=tmp_path)
    (tmp_path / "agents" / "static").mkdir(parents=True)

    with (
        patch("kg.interfaces.cli.commands.serve.load_config", return_value=config),
        patch("uvicorn.run") as mock_uvicorn_run,
    ):
        run_serve_command(port=8765, dev=False, console=console)

    mock_uvicorn_run.assert_called_once_with(
        "agents.api:app",
        host="127.0.0.1",
        port=8765,
    )
    assert "Knowledge Graph Web UI" in console.export_text()
