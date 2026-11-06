"""Retrieval failures reach the caller with a reason: HTTP body, SSE event, CLI exit code."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from agents.api import app
from kg.retrieval.errors import EmbeddingError, IndexMissingError


def test_query_route_returns_503_with_reason() -> None:
    client = TestClient(app)
    with patch(
        "kg.application.services.search_service.search",
        side_effect=IndexMissingError("检索索引还没建", hint="运行 kg index rebuild"),
    ):
        response = client.post("/api/query", json={"query": "缓存"})
    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "index_missing", "message": "检索索引还没建", "hint": "运行 kg index rebuild",
    }


def test_typeahead_returns_error_field_instead_of_silent_empty() -> None:
    client = TestClient(app)
    with patch("kg.retrieval.service.search_full", side_effect=IndexMissingError("没建")):
        response = client.get("/api/typeahead", params={"q": "缓存", "type": "debug"})
    body = response.json()
    assert response.status_code == 200
    assert body["results"] == [] and body["error"]["code"] == "index_missing"


def test_ask_stream_emits_error_event_and_readable_token() -> None:
    from agents.ask import ask_stream

    with patch("agents.ask.search", side_effect=EmbeddingError("查询向量计算失败：模型没加载")):
        events = list(ask_stream("缓存击穿怎么办", config=object()))  # type: ignore[arg-type]
    kinds = [e["type"] for e in events]
    assert kinds == ["status", "error", "token", "sources", "done"]
    assert events[1]["data"]["code"] == "embedding_failed"
    assert "查询向量计算失败" in events[2]["data"]["text"]
