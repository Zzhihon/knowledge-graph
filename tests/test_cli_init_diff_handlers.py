"""Regression tests for extracted init/diff CLI handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from agents.cli import cli


def test_init_command_delegates_to_new_handler() -> None:
    runner = CliRunner()

    with patch("kg.interfaces.cli.commands.init.run_init_command") as mock_init:
        result = runner.invoke(cli, ["init"])

    assert result.exit_code == 0
    assert mock_init.call_count == 1


def test_diff_command_delegates_to_new_handler() -> None:
    runner = CliRunner()

    with patch("kg.interfaces.cli.commands.diff.run_diff_command") as mock_diff:
        result = runner.invoke(cli, ["diff", "ke-test", "--stats"])

    assert result.exit_code == 0
    mock_diff.assert_called_once()
    assert mock_diff.call_args.kwargs["entry_id"] == "ke-test"
    assert mock_diff.call_args.kwargs["stats_only"] is True


def test_init_handler_creates_missing_directories(tmp_path) -> None:
    from rich.console import Console

    from kg.interfaces.cli.commands.init import run_init_command

    (tmp_path / "config.yaml").write_text("project:\n  name: Test\n", encoding="utf-8")
    config = SimpleNamespace(
        vault_path=tmp_path,
        name="Knowledge Graph",
        author="tester",
    )
    console = Console(record=True)

    with patch("kg.interfaces.cli.commands.init.load_config", return_value=config):
        run_init_command(console=console)

    assert (tmp_path / "00-Index").is_dir()
    assert (tmp_path / "indexes" / "weaviate").is_dir()
    assert "初始化完成" in console.export_text()


def test_diff_handler_stats_mode_renders_stats_panel() -> None:
    from rich.console import Console

    from kg.interfaces.cli.commands.diff import run_diff_command

    console = Console(record=True)
    graph_store = MagicMock()
    graph_store.connect.return_value = None
    diff_store = MagicMock()
    diff_store.get_stats.return_value = {
        "total_changes": 2,
        "last_modified": "2026-04-01",
        "total_additions": 10,
        "total_deletions": 3,
    }

    with (
        patch(
            "kg.interfaces.cli.commands.diff.load_config",
            return_value=SimpleNamespace(),
        ),
        patch(
            "agents.graph_store.get_graph_store",
            return_value=graph_store,
        ),
        patch(
            "agents.diff_store.DiffStore",
            return_value=diff_store,
        ),
    ):
        run_diff_command(
            entry_id="ke-test",
            limit=10,
            stats_only=True,
            console=console,
        )

    graph_store.close.assert_called_once()
    assert "条目演化统计: ke-test" in console.export_text()
