"""Diff-based evolution tracking for knowledge entries.

Stores unified diffs in SurrealDB whenever entries are created,
modified, or deleted during sync. Enables viewing how an entry's
content has changed over time.
"""

from __future__ import annotations

import difflib
from datetime import UTC, datetime
from typing import Any

from agents.graph_store import GraphStore, _extract_rows


class DiffStore:
    """Manages entry_diff records via an existing GraphStore connection."""

    def __init__(self, gs: GraphStore) -> None:
        self._db = gs._db

    def init_schema(self) -> None:
        """Create the entry_diff table and indexes if they don't exist."""
        self._db.query("""
            DEFINE TABLE entry_diff SCHEMALESS;
            DEFINE INDEX idx_diff_entry ON entry_diff FIELDS entry_id;
        """)

    def record_change(
        self,
        entry_id: str,
        change_type: str,
        old_content: str,
        new_content: str,
        old_hash: str,
        new_hash: str,
    ) -> None:
        """Record a single change event with a unified diff.

        Args:
            entry_id: The knowledge entry ID.
            change_type: One of 'created', 'modified', 'deleted'.
            old_content: Previous version content (empty for 'created').
            new_content: New version content (empty for 'deleted').
            old_hash: Previous content hash.
            new_hash: New content hash.
        """
        # Generate unified diff
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile="old", tofile="new",
            lineterm="",
        ))
        diff_text = "\n".join(diff_lines)

        # Compute stats
        additions = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
        deletions = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))

        now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        self._db.query(
            """
            CREATE entry_diff SET
                entry_id = $entry_id,
                timestamp = $timestamp,
                change_type = $change_type,
                old_hash = $old_hash,
                new_hash = $new_hash,
                diff_text = $diff_text,
                content = $content,
                stats = $stats;
            """,
            {
                "entry_id": entry_id,
                "timestamp": now,
                "change_type": change_type,
                "old_hash": old_hash,
                "new_hash": new_hash,
                "diff_text": diff_text,
                "content": new_content,
                "stats": {"additions": additions, "deletions": deletions},
            },
        )

    def get_history(self, entry_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get change history for an entry, newest first.

        Args:
            entry_id: The knowledge entry ID.
            limit: Maximum number of records to return.

        Returns:
            List of diff records sorted by timestamp descending.
        """
        result = self._db.query(
            "SELECT * FROM entry_diff WHERE entry_id = $eid ORDER BY timestamp DESC LIMIT $lim;",
            {"eid": entry_id, "lim": limit},
        )
        return _extract_rows(result)

    def get_latest_content(self, entry_id: str) -> str | None:
        """Get the most recently stored content for an entry.

        Used to compute the diff against the new version during sync.

        Returns:
            The stored content string, or None if no record exists.
        """
        result = self._db.query(
            "SELECT content FROM entry_diff WHERE entry_id = $eid ORDER BY timestamp DESC LIMIT 1;",
            {"eid": entry_id},
        )
        rows = _extract_rows(result)
        if rows:
            return rows[0].get("content")
        return None

    def get_stats(self, entry_id: str) -> dict[str, Any]:
        """Get aggregate change statistics for an entry.

        Returns:
            Dict with total_changes, last_modified, total_additions,
            total_deletions.
        """
        result = self._db.query(
            "SELECT * FROM entry_diff WHERE entry_id = $eid ORDER BY timestamp DESC;",
            {"eid": entry_id},
        )
        rows = _extract_rows(result)

        if not rows:
            return {
                "total_changes": 0,
                "last_modified": None,
                "total_additions": 0,
                "total_deletions": 0,
            }

        total_additions = 0
        total_deletions = 0
        for row in rows:
            s = row.get("stats", {})
            if isinstance(s, dict):
                total_additions += s.get("additions", 0)
                total_deletions += s.get("deletions", 0)

        return {
            "total_changes": len(rows),
            "last_modified": rows[0].get("timestamp"),
            "total_additions": total_additions,
            "total_deletions": total_deletions,
        }
