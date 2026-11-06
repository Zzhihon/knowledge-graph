"""Regression tests for old/new CLI entrypoint compatibility."""

from __future__ import annotations


def test_new_cli_entrypoint_reexports_existing_click_app() -> None:
    from agents.cli import cli as legacy_cli
    from kg.interfaces.cli.main import cli as new_cli

    assert new_cli is legacy_cli


def test_cli_still_exposes_expected_commands() -> None:
    from kg.interfaces.cli.main import cli

    expected_commands = {"ask", "ingest", "query", "review", "stats", "sync"}
    assert expected_commands.issubset(set(cli.commands))


def test_new_service_surfaces_forward_to_legacy_modules() -> None:
    from agents.ask import ask as legacy_ask
    from agents.config import load_config as legacy_load_config
    from agents.sync_engine import incremental_sync as legacy_incremental_sync
    from kg.application.services.ask_service import ask as new_ask
    from kg.application.services.search_service import search as new_search
    from kg.application.services.sync_service import (
        incremental_sync as new_incremental_sync,
    )
    from kg.infrastructure.config.loader import load_config as new_load_config
    from kg.retrieval.service import search as local_search

    assert new_ask is legacy_ask
    assert new_search is local_search  # search moved to the local LanceDB index (#7)
    assert new_incremental_sync is legacy_incremental_sync
    assert new_load_config is legacy_load_config
