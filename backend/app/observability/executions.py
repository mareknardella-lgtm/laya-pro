"""Durable metadata-only history for tool executions; tool inputs and outputs are never stored."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Optional

from .safe_projection import safe_text

from pydantic import BaseModel, ConfigDict, Field

from ..storage.sqlite import SQLiteDatabase


class ExecutionHistoryStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    project_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    workflow_id: Optional[str] = Field(default=None, max_length=128)
    step_id: Optional[str] = Field(default=None, max_length=64)
    tool_id: str = Field(min_length=1, max_length=128)
    status: ExecutionHistoryStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = Field(default=None, ge=0)
    error_code: Optional[str] = Field(default=None, max_length=128)
    error_summary: Optional[str] = Field(default=None, max_length=256)


class ExecutionPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExecutionRecord]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class RuntimeComponentStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    status: str
    last_attempt_at: Optional[datetime]
    last_successful_at: Optional[datetime]
    last_latency_ms: Optional[int]
    last_error_code: Optional[str]
    native_runtime_verified: bool = False


class ExecutionDiagnosticEvent(BaseModel):
    """Allowlisted audit metadata only; arbitrary event details are not exposed."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    created_at: datetime
    event_type: str
    workflow_id: Optional[str] = None
    execution_id: Optional[str] = None
    resource_id: Optional[str] = None
    status: str


class WorkflowExecutionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    project_id: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime
    pending_step: Optional[str] = None
    steps: list[dict[str, str]] = Field(default_factory=list)


class ExecutionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution: ExecutionRecord
    workflow: Optional[WorkflowExecutionSummary] = None
    events: list[ExecutionDiagnosticEvent] = Field(default_factory=list)


class ExecutionHistoryError(RuntimeError):
    """Execution lifecycle metadata could not be stored or loaded safely."""


class ExecutionHistoryStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def start(
        self,
        execution_id: str,
        idempotency_key: str,
        request_id: str,
        project_id: Optional[str],
        workflow_id: Optional[str],
        step_id: Optional[str],
        tool_id: str,
        started_at: datetime,
    ) -> None:
        if started_at.tzinfo is None:
            raise ValueError("Execution timestamps must be timezone-aware")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", execution_id) or not re.fullmatch(r"[A-Za-z0-9_.:-]+", idempotency_key[:128]):
            raise ValueError("Execution IDs contain unsupported characters")
        safe_request_id = request_id[:128]
        safe_project_id = project_id[:64] if project_id else "__unknown__"
        if safe_project_id != "__unknown__" and not re.fullmatch(r"[A-Za-z0-9_-]+", safe_project_id):
            raise ValueError("Execution project scope contains unsupported characters")
        safe_workflow_id = workflow_id[:128] if workflow_id else None
        safe_step_id = step_id[:64] if step_id else None
        safe_tool_id = tool_id[:128]
        if any(value and not re.fullmatch(r"[A-Za-z0-9_.:-]+", value) for value in (safe_request_id, safe_project_id, safe_workflow_id, safe_step_id, safe_tool_id)):
            raise ValueError("Execution identifiers contain unsupported characters")
        with self._database.transaction() as connection:
            connection.execute(
                """INSERT INTO execution_records (
                    execution_id, idempotency_key, request_id, project_id, workflow_id, step_id, tool_id, status,
                    started_at, completed_at, duration_ms, error_code, error_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)""",
                (execution_id[:128], idempotency_key[:128], safe_request_id, safe_project_id, safe_workflow_id, safe_step_id, safe_tool_id, ExecutionHistoryStatus.RUNNING.value, started_at.astimezone(timezone.utc).isoformat()),
            )

    def finish(
        self,
        execution_id: str,
        status: ExecutionHistoryStatus,
        completed_at: datetime,
        duration_ms: int,
        error_code: Optional[str] = None,
        error_summary: Optional[str] = None,
    ) -> None:
        if status == ExecutionHistoryStatus.RUNNING:
            raise ValueError("A finished execution cannot remain running")
        if completed_at.tzinfo is None:
            raise ValueError("Execution timestamps must be timezone-aware")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", execution_id):
            raise ValueError("Execution ID contains unsupported characters")
        safe_summary = safe_text(error_summary, max_length=256) if error_summary is not None else None
        safe_code = error_code[:128] if error_code is not None else None
        if safe_code is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]+", safe_code):
            safe_code = "execution_error"
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE execution_records SET status = ?, completed_at = ?, duration_ms = ?,
                    error_code = ?, error_summary = ?
                WHERE execution_id = ? AND status = ?""",
                (
                    status.value,
                    completed_at.astimezone(timezone.utc).isoformat(),
                    max(0, duration_ms),
                    safe_code,
                    safe_summary,
                    execution_id,
                    ExecutionHistoryStatus.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ExecutionHistoryError("Execution history record is missing or already finished")

    def get(self, execution_id: str) -> ExecutionRecord:
        with closing(self._database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM execution_records WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        if row is None:
            raise ExecutionHistoryError("Execution record does not exist")
        return self._from_row(row)

    def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        query: Optional[str] = None,
        status: Optional[ExecutionHistoryStatus] = None,
        workflow_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        project_ids: Optional[list[str]] = None,
    ) -> tuple[list[ExecutionRecord], int]:
        bounded_limit = max(1, min(limit, 200))
        bounded_offset = max(0, min(offset, 100_000))
        clauses: list[str] = []
        values: list[object] = []
        if query:
            clauses.append("(execution_id LIKE ? ESCAPE '\\' OR workflow_id LIKE ? ESCAPE '\\')")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.extend((f"%{escaped}%", f"%{escaped}%"))
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value)
        if workflow_id:
            clauses.append("workflow_id = ?")
            values.append(workflow_id)
        if project_ids is not None:
            scoped_ids = list(dict.fromkeys(project_ids))[:900]
            if scoped_ids:
                clauses.append(f"project_id IN ({','.join('?' for _ in scoped_ids)})")
                values.extend(scoped_ids)
            else:
                clauses.append("1 = 0")
        if since is not None:
            clauses.append("started_at >= ?")
            values.append(since.astimezone(timezone.utc).isoformat())
        if until is not None:
            clauses.append("started_at <= ?")
            values.append(until.astimezone(timezone.utc).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._database.connect()) as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS count FROM execution_records {where}",
                values,
            ).fetchone()["count"]
            rows = connection.execute(
                f"SELECT * FROM execution_records {where} ORDER BY started_at DESC, execution_id DESC LIMIT ? OFFSET ?",
                [*values, bounded_limit, bounded_offset],
            ).fetchall()
        return [self._from_row(row) for row in rows], int(total)

    @staticmethod
    def _from_row(row) -> ExecutionRecord:
        return ExecutionRecord(
            execution_id=row["execution_id"],
            request_id=row["request_id"],
            project_id=row["project_id"] if "project_id" in row.keys() else None,
            workflow_id=row["workflow_id"],
            step_id=row["step_id"],
            tool_id=row["tool_id"],
            status=ExecutionHistoryStatus(row["status"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            duration_ms=row["duration_ms"],
            error_code=row["error_code"],
            error_summary=row["error_summary"],
        )
