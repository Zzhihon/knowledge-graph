"""Regression tests for the integration job worker migration."""

from __future__ import annotations

from unittest.mock import patch

from agents.api_routes.integrate import IntegrateJob, _run_job


def test_run_job_consumes_new_entry_integration_workflow_surface() -> None:
    job = IntegrateJob(job_id="job-1", params={"entry_ids": ["ke-1"], "dry_run": True})

    events = iter(
        [
            {"event": "start", "total": 1},
            {"event": "complete", "total_entries": 1},
        ]
    )

    with patch(
        "kg.application.workflows.entry_integration.integrate_entries",
        return_value=events,
    ) as mock_integrate_entries:
        _run_job(job)

    mock_integrate_entries.assert_called_once_with(entry_ids=["ke-1"], dry_run=True)
    assert job.status == "complete"
    assert [event["event"] for event in job.events] == ["start", "complete"]
