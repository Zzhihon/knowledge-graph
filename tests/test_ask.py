"""Tests for the RAG question-answering module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agents.ask import _build_context, _load_entry_content


class TestLoadEntryContent:
    """Tests for _load_entry_content helper."""

    def test_nonexistent_file_returns_empty(self, tmp_path):
        result = _load_entry_content(str(tmp_path / "nonexistent.md"))
        assert result == ""

    def test_reads_file_content(self, tmp_path):
        md = tmp_path / "entry.md"
        md.write_text("# Title\nSome content", encoding="utf-8")
        result = _load_entry_content(str(md))
        assert "# Title" in result
        assert "Some content" in result


class TestBuildContext:
    """Tests for _build_context prompt assembly."""

    def test_builds_context_from_results(self, tmp_path):
        """Context includes entry ID, title, domain, and content."""
        md = tmp_path / "entry.md"
        md.write_text("Body text here", encoding="utf-8")

        results = [
            {
                "id": "ke-20260226-test",
                "title": "Test Entry",
                "domain": "golang",
                "file_path": str(md),
                "score": 0.95,
            }
        ]

        config = MagicMock()
        context = _build_context(results, config, use_graph=False)

        assert "ke-20260226-test" in context
        assert "Test Entry" in context
        assert "golang" in context
        assert "Body text here" in context

    def test_handles_missing_file_gracefully(self):
        results = [
            {
                "id": "ke-missing",
                "title": "Missing",
                "domain": "unknown",
                "file_path": "/nonexistent/path.md",
                "score": 0.5,
            }
        ]

        config = MagicMock()
        context = _build_context(results, config, use_graph=False)

        assert "ke-missing" in context
        assert "(内容不可用)" in context

    def test_multiple_results_concatenated(self, tmp_path):
        for i in range(3):
            (tmp_path / f"e{i}.md").write_text(f"Content {i}", encoding="utf-8")

        results = [
            {
                "id": f"ke-{i}",
                "title": f"Entry {i}",
                "domain": "test",
                "file_path": str(tmp_path / f"e{i}.md"),
                "score": 0.9 - i * 0.1,
            }
            for i in range(3)
        ]

        config = MagicMock()
        context = _build_context(results, config, use_graph=False)

        for i in range(3):
            assert f"ke-{i}" in context
            assert f"Content {i}" in context

    def test_graph_disabled_no_neighbors(self, tmp_path):
        md = tmp_path / "entry.md"
        md.write_text("Body", encoding="utf-8")

        results = [
            {
                "id": "ke-test",
                "title": "Test",
                "domain": "test",
                "file_path": str(md),
                "score": 0.9,
            }
        ]

        config = MagicMock()
        context = _build_context(results, config, use_graph=False)

        assert "相关条目" not in context

    @patch("agents.ask._get_graph_neighbors")
    def test_graph_enabled_includes_neighbors(self, mock_neighbors, tmp_path):
        mock_neighbors.return_value = ["Neighbor A", "Neighbor B"]

        md = tmp_path / "entry.md"
        md.write_text("Body", encoding="utf-8")

        results = [
            {
                "id": "ke-test",
                "title": "Test",
                "domain": "test",
                "file_path": str(md),
                "score": 0.9,
            }
        ]

        config = MagicMock()
        context = _build_context(results, config, use_graph=True)

        assert "相关条目" in context
        assert "Neighbor A" in context
        assert "Neighbor B" in context


class TestAskIntegration:
    """Integration-level tests for the ask function (mocking Claude API)."""

    @patch("agents.ask.search")
    def test_no_results_prints_message(self, mock_search, capsys):
        mock_search.return_value = []

        from agents.ask import ask

        config = MagicMock()
        config.agent.model = "claude-sonnet-4-20250514"
        ask("some question", config=config)

        # Should not raise, and should indicate no results found
        # (Rich output goes to internal console, so we just verify no crash)

    @patch("agents.ask._stream_answer")
    @patch("agents.ask.search")
    def test_ask_calls_stream_with_context(self, mock_search, mock_stream, tmp_path):
        md = tmp_path / "entry.md"
        md.write_text("Knowledge content", encoding="utf-8")

        mock_search.return_value = [
            {
                "id": "ke-test",
                "title": "Test",
                "domain": "test",
                "file_path": str(md),
                "score": 0.9,
                "metadata": {},
            }
        ]

        from agents.ask import ask

        config = MagicMock()
        config.agent.model = "claude-sonnet-4-20250514"
        ask("what is this?", config=config, use_graph=False)

        mock_stream.assert_called_once()
        call_args = mock_stream.call_args
        assert "what is this?" in call_args[0][0]  # question
        assert "Knowledge content" in call_args[0][1]  # context
