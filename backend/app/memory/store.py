"""CRUD persistence for long-term memory entries."""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from ..storage.sqlite import Database
from .models import MemoryEntry, MemoryScope


def _entry(row: sqlite3.Row) -> MemoryEntry:
    return MemoryEntry(
        key=row["key"],
        value=row["value"],
        scope=MemoryScope(row["scope"]),
        pinned=bool(row["pinned"]),
        updated_at=row["updated_at"],
    )


class MemoryStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def put(self, entry: MemoryEntry) -> None:
        await self._db.run(self._put_sync, entry)

    def _put_sync(self, connection: sqlite3.Connection, entry: MemoryEntry) -> None:
        connection.execute(
            """
            INSERT INTO memory_entries (key, value, scope, pinned, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(key, scope) DO UPDATE SET
                value = excluded.value,
                pinned = MAX(memory_entries.pinned, excluded.pinned),
                updated_at = excluded.updated_at
            """,
            (entry.key, entry.value, entry.scope.value, int(entry.pinned)),
        )

    async def put_many(self, entries: List[MemoryEntry]) -> int:
        await self._db.run(self._put_many_sync, entries)
        return len(entries)

    def _put_many_sync(self, connection: sqlite3.Connection, entries: List[MemoryEntry]) -> None:
        connection.executemany(
            """
            INSERT INTO memory_entries (key, value, scope, pinned, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(key, scope) DO UPDATE SET
                value = excluded.value,
                pinned = MAX(memory_entries.pinned, excluded.pinned),
                updated_at = excluded.updated_at
            """,
            [(e.key, e.value, e.scope.value, int(e.pinned)) for e in entries],
        )

    async def all(self, scope: MemoryScope = MemoryScope.PERSISTENT) -> List[MemoryEntry]:
        rows = await self._db.run(self._all_sync, scope.value)
        return [_entry(row) for row in rows]

    def _all_sync(self, connection: sqlite3.Connection, scope: str) -> List[sqlite3.Row]:
        cursor = connection.execute(
            "SELECT key, value, scope, pinned, updated_at FROM memory_entries "
            "WHERE scope = ? ORDER BY pinned DESC, updated_at DESC",
            (scope,),
        )
        return cursor.fetchall()

    async def get(
        self, key: str, scope: MemoryScope = MemoryScope.PERSISTENT
    ) -> Optional[MemoryEntry]:
        row = await self._db.run(self._get_sync, key, scope.value)
        if row is None:
            return None
        return _entry(row)

    def _get_sync(self, connection: sqlite3.Connection, key: str, scope: str) -> Optional[sqlite3.Row]:
        cursor = connection.execute(
            "SELECT key, value, scope, pinned, updated_at FROM memory_entries "
            "WHERE key = ? AND scope = ?",
            (key, scope),
        )
        return cursor.fetchone()

    async def count(self, scope: MemoryScope = MemoryScope.PERSISTENT) -> int:
        return await self._db.run(self._count_sync, scope.value)

    def _count_sync(self, connection: sqlite3.Connection, scope: str) -> int:
        cursor = connection.execute(
            "SELECT COUNT(*) AS total FROM memory_entries WHERE scope = ?", (scope,)
        )
        return int(cursor.fetchone()["total"])

    async def delete(self, key: str, scope: MemoryScope = MemoryScope.PERSISTENT) -> bool:
        return await self._db.run(self._delete_sync, key, scope.value)

    def _delete_sync(self, connection: sqlite3.Connection, key: str, scope: str) -> bool:
        cursor = connection.execute(
            "DELETE FROM memory_entries WHERE key = ? AND scope = ?", (key, scope)
        )
        return cursor.rowcount > 0

    @staticmethod
    def parse_extraction(text: str) -> List[MemoryEntry]:
        """Parse the 'key: value' lines the fast model is asked to produce."""

        entries: List[MemoryEntry] = []
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("-*• ").strip()
            if not line or line.upper() == "NONE" or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if not key or not value:
                continue
            if len(key) > 200 or len(value) > 4_000:
                continue
            entries.append(MemoryEntry(key=key, value=value))
        return entries