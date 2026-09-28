"""SQLite workflow state repository with bounded optimistic checkpoints."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import ValidationError

from ..observability.audit import AuditEvent, AuditLog
from ..storage.sqlite import SQLiteDatabase
from .models import WorkflowDefinition, WorkflowRecord, WorkflowStatus


class WorkflowStateError(ValueError):
    """Workflow state is missing or cannot transition safely."""


class WorkflowStateStore:
    def __init__(self, database: SQLiteDatabase, audit: AuditLog) -> None:
        self._database = database
        self._audit = audit

    def create(self, workflow_id: str, definition: WorkflowDefinition) -> WorkflowRecord:
        now = datetime.now(timezone.utc)
        state: dict[str, Any] = {"completed_steps": [], "step_results": {}, "approval_requests": {}, "approval_ids": {}, "error": None}
        definition_json = definition.model_dump_json()
        state_json = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._database.transaction() as connection:
            connection.execute(
                """INSERT INTO workflow_records (workflow_id, project_id, status, definition_json, state_json, created_at, updated_at, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                (workflow_id, definition.project_id, WorkflowStatus.CREATED.value, definition_json, state_json, now.isoformat(), now.isoformat()),
            )
        self._audit.record(AuditEvent(
            actor_id="local-operator",
            event_type="workflow.created",
            project_id=definition.project_id,
            workflow_id=workflow_id,
            resource_id=workflow_id,
            status=WorkflowStatus.CREATED.value,
            details={"project_id": definition.project_id, "step_count": len(definition.steps)},
        ))
        return WorkflowRecord(
            workflow_id=workflow_id,
            project_id=definition.project_id,
            status=WorkflowStatus.CREATED,
            definition=definition,
            state=state,
            created_at=now,
            updated_at=now,
            version=1,
        )

    def get(self, workflow_id: str) -> WorkflowRecord:
        with closing(self._database.connect()) as connection:
            row = connection.execute("SELECT * FROM workflow_records WHERE workflow_id = ?", (workflow_id,)).fetchone()
        if row is None:
            raise WorkflowStateError("Workflow does not exist")
        return self._from_row(row)

    def list(self, limit: int = 100, project_ids: Optional[list[str]] = None) -> list[WorkflowRecord]:
        bounded = max(1, min(limit, 500))
        clauses: list[str] = []
        values: list[object] = []
        if project_ids is not None:
            scoped = list(dict.fromkeys(project_ids))[:900]
            if scoped:
                clauses.append(f"project_id IN ({','.join('?' for _ in scoped)})")
                values.extend(scoped)
            else:
                clauses.append("1 = 0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._database.connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM workflow_records {where} ORDER BY created_at DESC LIMIT ?",
                [*values, bounded],
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def checkpoint(
        self,
        workflow_id: str,
        state: dict[str, Any],
        status: WorkflowStatus,
        expected_version: Optional[int] = None,
        event_type: Optional[str] = None,
    ) -> WorkflowRecord:
        encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded) > 65_536:
            raise WorkflowStateError("Workflow state exceeds its configured storage bound")
        now = datetime.now(timezone.utc)
        with self._database.transaction() as connection:
            row = connection.execute("SELECT * FROM workflow_records WHERE workflow_id = ?", (workflow_id,)).fetchone()
            if row is None:
                raise WorkflowStateError("Workflow does not exist")
            version = row["version"]
            if expected_version is not None and version != expected_version:
                raise WorkflowStateError("Workflow state changed concurrently")
            allowed = {
                WorkflowStatus.CREATED: {WorkflowStatus.CREATED, WorkflowStatus.RUNNING, WorkflowStatus.PAUSED, WorkflowStatus.CANCELLED},
                WorkflowStatus.RUNNING: {WorkflowStatus.RUNNING, WorkflowStatus.PAUSED, WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED},
                WorkflowStatus.PAUSED: {WorkflowStatus.PAUSED, WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED},
                WorkflowStatus.AWAITING_APPROVAL: {WorkflowStatus.RUNNING, WorkflowStatus.PAUSED, WorkflowStatus.CANCELLED, WorkflowStatus.AWAITING_APPROVAL},
                WorkflowStatus.COMPLETED: set(),
                WorkflowStatus.FAILED: set(),
                WorkflowStatus.CANCELLED: set(),
            }
            old_status = WorkflowStatus(row["status"])
            if status not in allowed[old_status]:
                raise WorkflowStateError(f"Invalid workflow transition: {old_status.value} -> {status.value}")
            cursor = connection.execute(
                "UPDATE workflow_records SET state_json = ?, status = ?, updated_at = ?, version = version + 1 WHERE workflow_id = ? AND version = ?",
                (encoded, status.value, now.isoformat(), workflow_id, version),
            )
            if cursor.rowcount != 1:
                raise WorkflowStateError("Workflow checkpoint lost a concurrent update")
            updated = connection.execute("SELECT * FROM workflow_records WHERE workflow_id = ?", (workflow_id,)).fetchone()
        if event_type:
            self._audit.record(AuditEvent(
                actor_id="system:workflow-engine",
                event_type=event_type,
                project_id=updated["project_id"],
                workflow_id=workflow_id,
                resource_id=workflow_id,
                status=status.value,
                details={"version": updated["version"]},
            ))
        return self._from_row(updated)

    @staticmethod
    def _from_row(row) -> WorkflowRecord:
        try:
            definition = WorkflowDefinition.model_validate_json(row["definition_json"])
            state = json.loads(row["state_json"])
        except (ValidationError, json.JSONDecodeError) as exc:
            raise WorkflowStateError("Persisted workflow data is invalid") from exc
        return WorkflowRecord(
            workflow_id=row["workflow_id"],
            project_id=row["project_id"],
            status=WorkflowStatus(row["status"]),
            definition=definition,
            state=state,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            version=row["version"],
        )
