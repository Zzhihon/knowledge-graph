"""Source state management for watermark persistence.

Tracks last fetch timestamps/cursors for each external source
to enable incremental pulling.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class SourceStateManager:
    """Manages watermark state for external sources."""

    def __init__(self, state_file: Path | None = None):
        """Initialize state manager.

        Args:
            state_file: Path to state YAML file.
                        Defaults to .kg/source_state.yaml in vault root.
        """
        if state_file is None:
            # Default to .kg/source_state.yaml
            from agents.config import load_config
            config = load_config()
            kg_dir = config.vault_path / ".kg"
            kg_dir.mkdir(exist_ok=True)
            state_file = kg_dir / "source_state.yaml"

        self.state_file = state_file
        self._state: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        """Load state from disk."""
        if not self.state_file.exists():
            return {}

        try:
            with open(self.state_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data or {}
        except Exception as exc:
            logger.warning(f"Failed to load source state: {exc}")
            return {}

    def _save(self) -> None:
        """Save state to disk."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                yaml.safe_dump(self._state, f, allow_unicode=True, sort_keys=False)
        except Exception as exc:
            logger.error(f"Failed to save source state: {exc}")

    def get_state(self, source_type: str, source_id: str) -> dict[str, Any] | None:
        """Get state for a specific source.

        Args:
            source_type: Type of source (e.g., "rss", "github", "slack").
            source_id: Unique identifier within that type (e.g., feed URL).

        Returns:
            State dict or None if not found.
        """
        return self._state.get(source_type, {}).get(source_id)

    def set_state(
        self,
        source_type: str,
        source_id: str,
        state: dict[str, Any],
    ) -> None:
        """Update state for a specific source.

        Args:
            source_type: Type of source.
            source_id: Unique identifier.
            state: State dict to persist.
        """
        if source_type not in self._state:
            self._state[source_type] = {}

        self._state[source_type][source_id] = state
        self._save()

    def delete_state(self, source_type: str, source_id: str) -> None:
        """Delete state for a specific source.

        Args:
            source_type: Type of source.
            source_id: Unique identifier.
        """
        if source_type in self._state and source_id in self._state[source_type]:
            del self._state[source_type][source_id]
            self._save()

    def list_sources(self, source_type: str | None = None) -> dict[str, Any]:
        """List all tracked sources.

        Args:
            source_type: Filter by source type, or None for all.

        Returns:
            Dict of {source_type: {source_id: state}}.
        """
        if source_type:
            return {source_type: self._state.get(source_type, {})}
        return self._state.copy()
