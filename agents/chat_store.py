"""SQLite3 persistence layer for chat conversations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.config import load_config


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatStore:
    """Thin wrapper around SQLite3 for conversation/message CRUD."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            config = load_config()
            db_path = config.vault_path / "indexes" / "chat.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """\
                CREATE TABLE IF NOT EXISTS conversations (
                    id          TEXT PRIMARY KEY,
                    title       TEXT NOT NULL,
                    mode        TEXT NOT NULL DEFAULT 'ask',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role            TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    sources_json    TEXT,
                    created_at      TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conv
                    ON messages(conversation_id);
                """
            )

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def create_conversation(self, title: str, mode: str = "ask") -> dict[str, Any]:
        cid = uuid.uuid4().hex
        now = _now_iso()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO conversations (id, title, mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (cid, title, mode, now, now),
            )
        return {"id": cid, "title": title, "mode": mode, "created_at": now, "updated_at": now}

    def list_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, title, mode, created_at, updated_at FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, cid: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, title, mode, created_at, updated_at FROM conversations WHERE id = ?",
                (cid,),
            ).fetchone()
            if row is None:
                return None
            conv = dict(row)
            msgs = conn.execute(
                "SELECT id, role, content, sources_json, created_at FROM messages WHERE conversation_id = ? ORDER BY id",
                (cid,),
            ).fetchall()
            conv["messages"] = [
                {
                    "id": m["id"],
                    "role": m["role"],
                    "content": m["content"],
                    "sources": json.loads(m["sources_json"]) if m["sources_json"] else None,
                    "created_at": m["created_at"],
                }
                for m in msgs
            ]
        return conv

    def update_conversation(self, cid: str, title: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, _now_iso(), cid),
            )
        return cur.rowcount > 0

    def delete_conversation(self, cid: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        now = _now_iso()
        sources_json = json.dumps(sources, ensure_ascii=False) if sources else None
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, content, sources_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, role, content, sources_json, now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
        return {"id": cur.lastrowid, "role": role, "content": content, "sources": sources, "created_at": now}
