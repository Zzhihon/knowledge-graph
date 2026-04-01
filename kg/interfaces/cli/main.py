"""Primary CLI entrypoint for the staged `kg` package migration.

This module intentionally forwards to the existing Click application so we can
move packaging and import surfaces first without changing runtime behavior.
Subsequent refactor passes can migrate command registration here incrementally.
"""

from __future__ import annotations

from agents.cli import cli

__all__ = ["cli"]
