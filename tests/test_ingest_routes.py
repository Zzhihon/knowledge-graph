"""Regression tests for ingest routes migrated onto kg service/workflow surfaces."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from agents.api import app
from agents.batch_ingest import BatchResult, FileResult


def test_single_ingest_route_uses_new_ingest_service_surface() -> None:
    client = TestClient(app)
    payload = [
        {
            "id": "ke-test",
            "title": "Test Entry",
            "target": "01-Principles/ke-test.md",
            "action": "create",
            "novelty_score": 0.8,
            "quality_score": 0.9,
            "reason": "",
        }
    ]

    with patch(
        "kg.application.services.ingest_service.ingest_file_with_quality",
        return_value=payload,
    ) as mock_ingest:
        response = client.post(
            "/api/ingest",
            files={"file": ("note.md", b"# title\nbody", "text/markdown")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["merged"] == 0
    assert body["entries"][0]["id"] == "ke-test"
    mock_ingest.assert_called_once()


def test_batch_ingest_route_uses_new_batch_workflow_surface() -> None:
    client = TestClient(app)
    batch_result = BatchResult(
        total_files=2,
        processed=2,
        entries_created=1,
        entries_merged=1,
        entries_skipped=0,
        errors=[],
        file_results=[
            FileResult(
                file_path="/tmp/a.md",
                status="success",
                entries_created=1,
                entries=[{"id": "ke-a"}],
            ),
            FileResult(
                file_path="/tmp/b.md",
                status="success",
                entries_merged=1,
                entries=[{"id": "ke-b"}],
            ),
        ],
    )

    with patch(
        "kg.application.workflows.batch_ingest.ingest_files",
        return_value=batch_result,
    ) as mock_batch_ingest:
        response = client.post(
            "/api/ingest/batch",
            files=[
                ("files", ("a.md", b"A", "text/markdown")),
                ("files", ("b.md", b"B", "text/markdown")),
            ],
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total_files"] == 2
    assert body["entries_created"] == 1
    assert body["entries_merged"] == 1
    assert len(body["file_results"]) == 2
    mock_batch_ingest.assert_called_once()
