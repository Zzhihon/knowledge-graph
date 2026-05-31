"""Regression tests for extracted graph/backlinks CLI handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from agents.cli import cli


def test_graph_command_delegates_to_new_handler() -> None:
    runner = CliRunner()

    with patch("kg.interfaces.cli.commands.graph.run_graph_command") as mock_graph:
        result = runner.invoke(cli, ["graph", "ke-test", "--depth", "3"])

    assert result.exit_code == 0
    mock_graph.assert_called_once()
    assert mock_graph.call_args.kwargs["entry_id"] == "ke-test"
    assert mock_graph.call_args.kwargs["depth"] == 3


def test_backlinks_command_delegates_to_new_handler() -> None:
    runner = CliRunner()

    with patch("kg.interfaces.cli.commands.graph.run_backlinks_command") as mock_backlinks:
        result = runner.invoke(cli, ["backlinks", "ke-test"])

    assert result.exit_code == 0
    mock_backlinks.assert_called_once()
    assert mock_backlinks.call_args.kwargs["entry_id"] == "ke-test"


def test_backlinks_handler_prints_total_count() -> None:
    from rich.console import Console

    from kg.interfaces.cli.commands.graph import run_backlinks_command

    console = Console(record=True)

    with (
        patch("agents.backlinks.find_backlinks", return_value=[{"id": "a"}, {"id": "b"}]),
        patch("agents.backlinks.print_backlinks") as mock_print,
    ):
        run_backlinks_command(entry_id="ke-test", console=console)

    mock_print.assert_called_once()
    assert "共 2 个条目引用了 ke-test" in console.export_text()


def test_graph_handler_mermaid_writes_output_file(tmp_path) -> None:
    from rich.console import Console

    from kg.interfaces.cli.commands.graph import run_graph_command

    console = Console(record=True)
    output = tmp_path / "graph.mmd"
    graph_store = MagicMock()
    graph_store.get_entry.return_value = {
        "title": "Test",
        "entry_type": "principle",
        "depth": "deep",
        "confidence": 0.8,
    }
    graph_store.connect.return_value = None

    with (
        patch(
            "kg.interfaces.cli.commands.graph.load_config",
            return_value=SimpleNamespace(vault_path=tmp_path),
        ),
        patch("agents.graph_store.get_graph_store", return_value=graph_store),
        patch("agents.graph_viz.build_graph_data", return_value={"nodes": [], "edges": []}),
        patch("agents.graph_viz.to_mermaid", return_value="graph TD\nA-->B"),
    ):
        run_graph_command(
            entry_id="ke-test",
            depth=2,
            fmt="mermaid",
            output=str(output),
            console=console,
        )

    assert output.read_text() == "graph TD\nA-->B"
    graph_store.close.assert_called_once()
