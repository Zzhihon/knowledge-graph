"""Tests for the diff store module."""

from __future__ import annotations

import difflib
from unittest.mock import MagicMock

import pytest

from agents.diff_store import DiffStore


@pytest.fixture()
def mock_gs():
    """Create a mock GraphStore with a mock db."""
    gs = MagicMock()
    gs._db = MagicMock()
    return gs


@pytest.fixture()
def diff_store(mock_gs):
    """Create a DiffStore backed by the mock GraphStore."""
    return DiffStore(mock_gs)


class TestDiffStoreInit:
    def test_init_schema_creates_table(self, diff_store, mock_gs):
        diff_store.init_schema()
        mock_gs._db.query.assert_called_once()
        call_sql = mock_gs._db.query.call_args[0][0]
        assert "DEFINE TABLE entry_diff" in call_sql
        assert "DEFINE INDEX idx_diff_entry" in call_sql


class TestRecordChange:
    def test_created_entry_records_diff(self, diff_store, mock_gs):
        diff_store.record_change(
            entry_id="ke-test",
            change_type="created",
            old_content="",
            new_content="line 1\nline 2\n",
            old_hash="",
            new_hash="abc123",
        )

        mock_gs._db.query.assert_called_once()
        call_args = mock_gs._db.query.call_args
        params = call_args[0][1]

        assert params["entry_id"] == "ke-test"
        assert params["change_type"] == "created"
        assert params["new_hash"] == "abc123"
        assert params["content"] == "line 1\nline 2\n"
        assert params["stats"]["additions"] >= 2

    def test_modified_entry_generates_diff(self, diff_store, mock_gs):
        old = "line 1\nline 2\nline 3\n"
        new = "line 1\nline 2 modified\nline 3\nline 4\n"

        diff_store.record_change(
            entry_id="ke-test",
            change_type="modified",
            old_content=old,
            new_content=new,
            old_hash="old",
            new_hash="new",
        )

        params = mock_gs._db.query.call_args[0][1]
        assert params["change_type"] == "modified"
        # Diff text should contain the modifications
        assert params["diff_text"]  # non-empty diff
        assert params["stats"]["additions"] > 0
        assert params["stats"]["deletions"] > 0

    def test_deleted_entry_records_diff(self, diff_store, mock_gs):
        diff_store.record_change(
            entry_id="ke-test",
            change_type="deleted",
            old_content="old content\n",
            new_content="",
            old_hash="old",
            new_hash="",
        )

        params = mock_gs._db.query.call_args[0][1]
        assert params["change_type"] == "deleted"
        assert params["stats"]["deletions"] >= 1


class TestGetHistory:
    def test_returns_query_results(self, diff_store, mock_gs):
        mock_gs._db.query.return_value = [
            {"result": [
                {"entry_id": "ke-test", "change_type": "modified", "timestamp": "2026-03-15"},
                {"entry_id": "ke-test", "change_type": "created", "timestamp": "2026-03-10"},
            ]}
        ]

        history = diff_store.get_history("ke-test")
        assert len(history) == 2

    def test_empty_history(self, diff_store, mock_gs):
        mock_gs._db.query.return_value = [{"result": []}]
        history = diff_store.get_history("ke-nonexistent")
        assert history == []


class TestGetLatestContent:
    def test_returns_content_when_exists(self, diff_store, mock_gs):
        mock_gs._db.query.return_value = [
            {"result": [{"content": "latest version content"}]}
        ]

        content = diff_store.get_latest_content("ke-test")
        assert content == "latest version content"

    def test_returns_none_when_no_records(self, diff_store, mock_gs):
        mock_gs._db.query.return_value = [{"result": []}]
        content = diff_store.get_latest_content("ke-nonexistent")
        assert content is None


class TestGetStats:
    def test_returns_aggregated_stats(self, diff_store, mock_gs):
        mock_gs._db.query.return_value = [
            {"result": [
                {
                    "entry_id": "ke-test",
                    "timestamp": "2026-03-15",
                    "stats": {"additions": 5, "deletions": 2},
                },
                {
                    "entry_id": "ke-test",
                    "timestamp": "2026-03-10",
                    "stats": {"additions": 10, "deletions": 0},
                },
            ]}
        ]

        stats = diff_store.get_stats("ke-test")
        assert stats["total_changes"] == 2
        assert stats["last_modified"] == "2026-03-15"
        assert stats["total_additions"] == 15
        assert stats["total_deletions"] == 2

    def test_empty_stats_for_unknown_entry(self, diff_store, mock_gs):
        mock_gs._db.query.return_value = [{"result": []}]
        stats = diff_store.get_stats("ke-nonexistent")
        assert stats["total_changes"] == 0
        assert stats["last_modified"] is None


class TestDiffGeneration:
    """Verify the diff logic produces correct unified diffs."""

    def test_diff_format_matches_unified(self):
        old = "line 1\nline 2\nline 3\n"
        new = "line 1\nline 2 changed\nline 3\nline 4\n"

        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile="old", tofile="new",
            lineterm="",
        ))

        diff_text = "\n".join(diff_lines)
        assert "---" in diff_text
        assert "+++" in diff_text
        assert "-line 2" in diff_text
        assert "+line 2 changed" in diff_text
