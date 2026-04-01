"""Regression tests for the staged FastAPI entrypoint migration."""

from __future__ import annotations


def test_new_api_entrypoint_reexports_existing_fastapi_app() -> None:
    from agents.api import app as legacy_app
    from kg.interfaces.api.app import app as new_app

    assert new_app is legacy_app


def test_api_still_exposes_core_routes() -> None:
    from kg.interfaces.api.app import app

    route_paths = {route.path for route in app.routes}
    assert {"/api/query", "/api/ask", "/api/sync"}.issubset(route_paths)
