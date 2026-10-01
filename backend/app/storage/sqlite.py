"""SQLite schema and connection helper.

Standard library only: the database is local, small and single-writer, so an
async driver would add a dependency without buying anything.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id   TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_active  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content      TEXT NOT NULL,
    tier         TEXT,
    meta         TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON chat_messages(session_id, id);

CREATE TABLE IF NOT EXISTS memory_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    scope      TEXT NOT NULL DEFAULT 'persistent',
    pinned     INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (key, scope)
);

CREATE TABLE IF NOT EXISTS orchestration_traces (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    tier       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Database:
    """Thin async wrapper over a single SQLite connection."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._initialized = False

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self._path), timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    async def initialize(self) -> None:
        await self.run(self._migrate)

    async def run(self, operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run a blocking DB operation off the event loop."""

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._run_sync, operation, args, kwargs)

    def _run_sync(
        self,
        operation: Callable[..., T],
        args: tuple = (),
        kwargs: Optional[dict] = None,
    ) -> T:
        connection = self._connect()
        try:
            result = operation(connection, *args, **(kwargs or {}))
            connection.commit()
            return result
        finally:
            connection.close()

    def _migrate(self, connection: sqlite3.Connection) -> None:
        connection.executescript(_SCHEMA)
        # Idempotent upgrade: databases created before 'pinned' existed still work.
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(memory_entries)")
        }
        if "pinned" not in columns:
            connection.execute(
                "ALTER TABLE memory_entries ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
            )

    def close(self) -> None:
        self._initialized = False