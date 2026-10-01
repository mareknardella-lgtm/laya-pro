"""Chat session and message persistence."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, List, Optional

from ..storage.sqlite import Database


class ChatHistoryManager:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def ensure_session(self, session_id: str) -> None:
        await self._db.run(self._ensure_session_sync, session_id)

    def _ensure_session_sync(self, connection: sqlite3.Connection, session_id: str) -> None:
        connection.execute(
            """
            INSERT INTO chat_sessions (session_id) VALUES (?)
            ON CONFLICT(session_id) DO UPDATE SET last_active = datetime('now')
            """,
            (session_id,),
        )

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tier: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> None:
        await self._db.run(
            self._add_message_sync, session_id, role, content, tier, meta or {}
        )

    def _add_message_sync(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        role: str,
        content: str,
        tier: Optional[str],
        meta: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO chat_sessions (session_id) VALUES (?)", (session_id,)
        )
        connection.execute(
            """
            INSERT INTO chat_messages (session_id, role, content, tier, meta)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, role, content, tier, json.dumps(meta, ensure_ascii=False, default=str)),
        )
        connection.execute(
            "UPDATE chat_sessions SET last_active = datetime('now') WHERE session_id = ?",
            (session_id,),
        )

    async def get_messages(
        self, session_id: str, limit: int = 10, direction: str = "desc"
    ) -> List[dict]:
        """Most recent messages by default; direction='asc' returns the oldest."""

        if direction not in ("asc", "desc"):
            raise ValueError("direction must be 'asc' or 'desc'")
        rows = await self._db.run(self._get_messages_sync, session_id, limit, direction)
        return [dict(row) for row in rows]

    def _get_messages_sync(
        self, connection: sqlite3.Connection, session_id: str, limit: int, direction: str
    ) -> List[sqlite3.Row]:
        inner = "DESC" if direction == "desc" else "ASC"
        cursor = connection.execute(
            f"""
            SELECT role, content, tier, created_at
            FROM (
                SELECT role, content, tier, created_at, id
                FROM chat_messages WHERE session_id = ?
                ORDER BY id {inner} LIMIT ?
            ) ORDER BY id ASC
            """,
            (session_id, max(1, limit)),
        )
        return cursor.fetchall()

    async def list_sessions(self, limit: int = 20) -> List[dict]:
        rows = await self._db.run(self._list_sessions_sync, limit)
        return [dict(row) for row in rows]

    async def add_trace(self, session_id: str, tier: str, payload: dict) -> None:
        """Persist the pipeline trace so an answer can be audited after the fact."""

        await self._db.run(self._add_trace_sync, session_id, tier, payload)

    def _add_trace_sync(
        self, connection: sqlite3.Connection, session_id: str, tier: str, payload: dict
    ) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO chat_sessions (session_id) VALUES (?)", (session_id,)
        )
        connection.execute(
            "INSERT INTO orchestration_traces (session_id, tier, payload) VALUES (?, ?, ?)",
            (session_id, tier, json.dumps(payload, ensure_ascii=False, default=str)),
        )

    async def get_traces(self, session_id: str, limit: int = 20) -> List[dict]:
        rows = await self._db.run(self._get_traces_sync, session_id, limit)
        traces = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item["payload"])
            except ValueError:
                pass
            traces.append(item)
        return traces

    def _get_traces_sync(
        self, connection: sqlite3.Connection, session_id: str, limit: int
    ) -> List[sqlite3.Row]:
        cursor = connection.execute(
            """
            SELECT id, tier, payload, created_at
            FROM orchestration_traces
            WHERE session_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (session_id, max(1, limit)),
        )
        return cursor.fetchall()

    def _list_sessions_sync(self, connection: sqlite3.Connection, limit: int) -> List[sqlite3.Row]:
        cursor = connection.execute(
            """
            SELECT s.session_id, s.created_at, s.last_active, COUNT(m.id) AS message_count
            FROM chat_sessions s
            LEFT JOIN chat_messages m ON m.session_id = s.session_id
            GROUP BY s.session_id
            ORDER BY s.last_active DESC
            LIMIT ?
            """,
            (max(1, limit),),
        )
        return cursor.fetchall()