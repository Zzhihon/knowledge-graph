"""Primary FastAPI entrypoint for the staged `kg` package migration."""

from __future__ import annotations

from agents.api import app

__all__ = ["app"]
