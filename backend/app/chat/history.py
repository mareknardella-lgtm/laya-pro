import sqlite3
import uuid
from datetime import datetime, timezone
import json

class ChatHistoryManager:
    def __init__(self, connection: sqlite3.Connection):
        self._conn = connection

    def create_session(self, title: str) -> str:
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO chat_sessions (session_id, created_at, updated_at, title, status) VALUES (?, ?, ?, ?, ?)",
            (session_id, now, now, title, "active")
        )
        return session_id

    def get_session(self, session_id: str) -> dict:
        row = self._conn.execute("SELECT * FROM chat_sessions WHERE session_id = ?", (session_id,)).fetchone()
        if not row:
            return {}
        return dict(row)

    def list_sessions(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))

    def add_message(self, session_id: str, role: str, content: str, mode: str, metadata: dict) -> None:
        message_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO chat_messages (message_id, session_id, role, content, mode, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, session_id, role, content, mode, json.dumps(metadata), now)
        )
        self._conn.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?",
            (now, session_id)
        )

    def get_messages(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,)
        ).fetchall()
        return [dict(row) for row in rows]
