"""Regression tests for API routes migrated onto kg service surfaces."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from agents.api import app


def test_query_route_uses_new_search_service_surface() -> None:
    client = TestClient(app)

    payload = [{"id": "ke-test", "score": 0.9, "title": "Test"}]
    with patch(
        "kg.application.services.search_service.search",
        return_value=payload,
    ) as mock_search:
        response = client.post("/api/query", json={"query": "mcp", "top_k": 3})

    assert response.status_code == 200
    assert response.json() == payload
    mock_search.assert_called_once_with(query="mcp", filters=None, top_k=3)


def test_ask_route_streams_events_from_new_ask_service_surface() -> None:
    client = TestClient(app)

    events = [
        {"type": "status", "data": {"message": "searching"}},
        {"type": "done", "data": {}},
    ]
    with patch(
        "kg.application.services.ask_service.ask_stream",
        return_value=iter(events),
    ) as mock_stream:
        response = client.post("/api/ask", json={"question": "what is mcp?"})

    assert response.status_code == 200
    assert 'event: status' in response.text
    assert 'event: done' in response.text
    mock_stream.assert_called_once_with(question="what is mcp?", top_k=5, domain=None)


def test_sync_route_uses_new_sync_service_surface() -> None:
    client = TestClient(app)

    payload = {"new": 1, "changed": 0, "deleted": 0, "unchanged": 0}
    with patch(
        "kg.application.services.sync_service.incremental_sync",
        return_value=payload,
    ) as mock_sync:
        response = client.post("/api/sync", json={"full": False})

    assert response.status_code == 200
    assert response.json() == payload
    mock_sync.assert_called_once_with()
