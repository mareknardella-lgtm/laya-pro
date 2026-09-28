"""SQLite-backed persistent project memory with explicit per-project CRUD."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from ..storage.sqlite import SQLiteDatabase
from .models import MemoryEntry, MemoryScope


class MemoryStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def put(self, project_id: str, key: str, value: Any) -> MemoryEntry:
        now = datetime.now(timezone.utc)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._database.transaction() as connection:
            existing = connection.execute(
                "SELECT entry_id, created_at FROM memory_entries WHERE project_id = ? AND memory_key = ?",
                (project_id, key),
            ).fetchone()
            entry_id = existing["entry_id"] if existing else str(uuid4())
            created_at = existing["created_at"] if existing else now.isoformat()
            connection.execute(
                """INSERT INTO memory_entries (entry_id, project_id, memory_key, value_json, sensitivity, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'normal', ?, ?)
                ON CONFLICT(project_id, memory_key) DO UPDATE SET
                    value_json = excluded.value_json, sensitivity = excluded.sensitivity, updated_at = excluded.updated_at""",
                (entry_id, project_id, key, encoded, created_at, now.isoformat()),
            )
        return MemoryEntry(
            project_id=project_id,
            key=key,
            value=value,
            scope=MemoryScope.PERSISTENT,
            created_at=datetime.fromisoformat(created_at),
            updated_at=now,
        )

    def get(self, project_id: str, key: str) -> Optional[MemoryEntry]:
        with closing(self._database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM memory_entries WHERE project_id = ? AND memory_key = ?",
                (project_id, key),
            ).fetchone()
        if row is None:
            return None
        return MemoryEntry(
            project_id=row["project_id"],
            key=row["memory_key"],
            value=json.loads(row["value_json"]),
            scope=MemoryScope.PERSISTENT,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list(self, project_id: str, limit: int = 100) -> list[MemoryEntry]:
        bounded = max(1, min(limit, 500))
        with closing(self._database.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM memory_entries WHERE project_id = ? ORDER BY memory_key LIMIT ?",
                (project_id, bounded),
            ).fetchall()
        return [
            MemoryEntry(
                project_id=row["project_id"],
                key=row["memory_key"],
                value=json.loads(row["value_json"]),
                scope=MemoryScope.PERSISTENT,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    def delete(self, project_id: str, key: str) -> bool:
        with self._database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM memory_entries WHERE project_id = ? AND memory_key = ?",
                (project_id, key),
            )
        return cursor.rowcount == 1
