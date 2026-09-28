"""Local SQLite schema and connection/transaction boundaries."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterator
from contextlib import closing, contextmanager


class SQLiteDatabase:
    """SQLite storage for local single-host deployment; never a network database."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._lock, closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    request_id TEXT,
                    workflow_id TEXT,
                    decision_id TEXT,
                    execution_id TEXT,
                    resource_id TEXT,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_events(request_id);
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tool_id TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    project_id TEXT,
                    workflow_id TEXT,
                    request_id TEXT,
                    parameters_preview_json TEXT NOT NULL DEFAULT '{}',
                    requested_by TEXT NOT NULL,
                    decided_by TEXT,
                    decided_at TEXT,
                    reason TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_approvals_status_expiry ON approvals(status, expires_at);
                CREATE INDEX IF NOT EXISTS idx_approvals_action ON approvals(tool_id, input_fingerprint, status);
                CREATE TABLE IF NOT EXISTS change_records (
                    operation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    backup_id TEXT NOT NULL,
                    existed_before INTEGER NOT NULL,
                    before_sha256 TEXT NOT NULL,
                    after_sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_changes_project ON change_records(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS memory_entries (
                    entry_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, memory_key)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_project ON memory_entries(project_id);
                CREATE TABLE IF NOT EXISTS workflow_records (
                    workflow_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    status TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS execution_records (
                    execution_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    project_id TEXT,
                    workflow_id TEXT,
                    step_id TEXT,
                    tool_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    duration_ms INTEGER,
                    error_code TEXT,
                    error_summary TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_execution_started ON execution_records(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_execution_workflow ON execution_records(workflow_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_execution_status ON execution_records(status, started_at DESC);
                CREATE TABLE IF NOT EXISTS decision_events (
                    decision_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    workflow_id TEXT,
                    project_id TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    action_id TEXT,
                    execution_status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_decision_created ON decision_events(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_decision_workflow ON decision_events(workflow_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS system_health_checks (
                    checked_at TEXT PRIMARY KEY,
                    api_status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at DESC);
                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    mode TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, created_at ASC);
                """
            )
            audit_columns = {row["name"] for row in connection.execute("PRAGMA table_info(audit_events)").fetchall()}
            if "project_id" not in audit_columns:
                connection.execute("ALTER TABLE audit_events ADD COLUMN project_id TEXT")
            execution_columns = {row["name"] for row in connection.execute("PRAGMA table_info(execution_records)").fetchall()}
            if "idempotency_key" not in execution_columns:
                connection.execute("ALTER TABLE execution_records ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''")
            if "project_id" not in execution_columns:
                connection.execute("ALTER TABLE execution_records ADD COLUMN project_id TEXT")
                connection.execute(
                    """UPDATE execution_records SET project_id = (
                        SELECT workflow_records.project_id FROM workflow_records
                        WHERE workflow_records.workflow_id = execution_records.workflow_id
                    ) WHERE workflow_id IS NOT NULL"""
                )
                connection.execute(
                    """UPDATE execution_records SET project_id = '__unknown__'
                    WHERE workflow_id IS NOT NULL AND project_id IS NULL"""
                )
                connection.execute(
                    "UPDATE execution_records SET project_id = '__unknown__' WHERE workflow_id IS NULL"
                )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_execution_project ON execution_records(project_id, started_at DESC)")
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(approvals)").fetchall()}
            if "parameters_preview_json" not in columns:
                connection.execute("ALTER TABLE approvals ADD COLUMN parameters_preview_json TEXT NOT NULL DEFAULT '{}' ")
            change_columns = {row["name"] for row in connection.execute("PRAGMA table_info(change_records)").fetchall()}
            if "size_bytes" not in change_columns:
                connection.execute("ALTER TABLE change_records ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def transaction(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
