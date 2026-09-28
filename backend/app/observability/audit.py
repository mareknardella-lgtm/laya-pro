"""Append-only local audit events with conservative metadata redaction."""

from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..storage.sqlite import SQLiteDatabase
from .safe_projection import safe_text

_SECRET_KEY = re.compile(r"(secret|token|password|api[_-]?key|credential|authorization)", re.IGNORECASE)


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "[truncated]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:64]:
            key_text = str(key)[:128]
            result[key_text] = "[REDACTED]" if _SECRET_KEY.search(key_text) else _redact(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in value[:64]]
    if isinstance(value, str):
        return safe_text(value, max_length=2_000)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return safe_text(str(value), max_length=256)


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=128)
    request_id: Optional[str] = None
    project_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    workflow_id: Optional[str] = None
    decision_id: Optional[str] = None
    execution_id: Optional[str] = None
    resource_id: Optional[str] = None
    status: str = Field(min_length=1, max_length=64)
    details: dict[str, Any] = Field(default_factory=dict)


def _safe_details(serialized: str) -> dict[str, Any]:
    try:
        details = json.loads(serialized)
    except (TypeError, ValueError):
        return {"unavailable": "invalid_audit_details"}
    if not isinstance(details, dict):
        return {"unavailable": "invalid_audit_details"}
    return _redact(details)


def project_audit_scope_predicate() -> str:
    """SQL predicate that scopes audit metadata and fails closed on conflicting links."""
    project_scope = "(SELECT project_id FROM project_scope)"
    project_in_scope = f"project_id IN {project_scope}"
    detail_project_id = "(CASE WHEN json_valid(audit_events.details_json) THEN json_extract(audit_events.details_json, '$.project_id') END)"
    workflow_link = (
        f"audit_events.workflow_id IN (SELECT workflow_id FROM workflow_records WHERE {project_in_scope}) "
        f"OR audit_events.workflow_id IN (SELECT workflow_id FROM execution_records WHERE {project_in_scope} AND workflow_id IS NOT NULL)"
    )
    workflow_resource_link = (
        f"(audit_events.event_type LIKE 'workflow.%' AND audit_events.workflow_id IS NULL "
        f"AND audit_events.resource_id IN (SELECT workflow_id FROM workflow_records WHERE {project_in_scope}))"
    )
    legacy_decision_link = (
        f"(audit_events.event_type = 'decision.recorded' AND audit_events.decision_id IN "
        f"(SELECT decision_id FROM decision_events WHERE {project_in_scope}))"
    )
    memory_resource_link = (
        "(audit_events.event_type LIKE 'memory.%' AND EXISTS (SELECT 1 FROM project_scope "
        "WHERE substr(audit_events.resource_id, 1, length(project_scope.project_id) + 1) = project_scope.project_id || ':'))"
    )
    return (
        "((audit_events.project_id IN (SELECT project_id FROM project_scope) "
        f"OR ({workflow_link}) "
        f"OR audit_events.resource_id IN (SELECT approval_id FROM approvals WHERE {project_in_scope}) "
        f"OR audit_events.resource_id IN (SELECT operation_id FROM change_records WHERE {project_in_scope}) "
        f"OR audit_events.request_id IN (SELECT request_id FROM decision_events WHERE {project_in_scope} AND request_id IS NOT NULL) "
        f"OR audit_events.execution_id IN (SELECT execution_id FROM execution_records WHERE {project_in_scope}) "
        f"OR {detail_project_id} IN {project_scope} "
        f"OR {memory_resource_link} "
        f"OR {legacy_decision_link} "
        f"OR {workflow_resource_link}) "
        f"AND (audit_events.project_id IS NULL OR audit_events.project_id IN {project_scope}) "
        "AND NOT ("
        f"({detail_project_id} IS NOT NULL AND {detail_project_id} NOT IN {project_scope}) "
        "OR EXISTS (SELECT 1 FROM workflow_records AS scoped_workflow WHERE scoped_workflow.workflow_id = audit_events.workflow_id "
        f"AND (scoped_workflow.project_id IS NULL OR scoped_workflow.project_id NOT IN {project_scope})) "
        "OR EXISTS (SELECT 1 FROM execution_records AS scoped_workflow_execution WHERE scoped_workflow_execution.workflow_id = audit_events.workflow_id "
        f"AND (scoped_workflow_execution.project_id IS NULL OR scoped_workflow_execution.project_id NOT IN {project_scope})) "
        "OR EXISTS (SELECT 1 FROM workflow_records AS scoped_resource_workflow WHERE scoped_resource_workflow.workflow_id = audit_events.resource_id "
        f"AND (scoped_resource_workflow.project_id IS NULL OR scoped_resource_workflow.project_id NOT IN {project_scope})) "
        "OR EXISTS (SELECT 1 FROM approvals AS scoped_approval WHERE scoped_approval.approval_id = audit_events.resource_id "
        f"AND (scoped_approval.project_id IS NULL OR scoped_approval.project_id NOT IN {project_scope})) "
        "OR EXISTS (SELECT 1 FROM change_records AS scoped_change WHERE scoped_change.operation_id = audit_events.resource_id "
        f"AND scoped_change.project_id NOT IN {project_scope}) "
        "OR EXISTS (SELECT 1 FROM decision_events AS scoped_request WHERE scoped_request.request_id = audit_events.request_id "
        f"AND (scoped_request.project_id IS NULL OR scoped_request.project_id NOT IN {project_scope})) "
        "OR EXISTS (SELECT 1 FROM execution_records AS scoped_execution WHERE scoped_execution.execution_id = audit_events.execution_id "
        f"AND (scoped_execution.project_id IS NULL OR scoped_execution.project_id NOT IN {project_scope})) "
        "OR EXISTS (SELECT 1 FROM decision_events AS scoped_decision WHERE scoped_decision.decision_id = audit_events.decision_id "
        f"AND (scoped_decision.project_id IS NULL OR scoped_decision.project_id NOT IN {project_scope})) "
        "OR (audit_events.event_type = 'decision.recorded' AND EXISTS (SELECT 1 FROM decision_events AS scoped_decision_request "
        "WHERE scoped_decision_request.request_id = audit_events.request_id "
        f"AND (scoped_decision_request.project_id IS NULL OR scoped_decision_request.project_id NOT IN {project_scope})))"
        "))"
    )


class AuditLog:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def record(self, event: AuditEvent) -> AuditEvent:
        details = _redact(event.details)
        project_id = event.project_id or (details.get("project_id") if isinstance(details.get("project_id"), str) else None)
        serialized = json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(serialized) > 8_192:
            serialized = json.dumps({"truncated": True}, separators=(",", ":"))
        with self._database.transaction() as connection:
            connection.execute(
                """INSERT INTO audit_events (
                    event_id, created_at, actor_id, event_type, request_id, project_id, workflow_id,
                    decision_id, execution_id, resource_id, status, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id,
                    event.created_at.isoformat(),
                    event.actor_id,
                    event.event_type,
                    event.request_id,
                    project_id,
                    event.workflow_id,
                    event.decision_id,
                    event.execution_id,
                    event.resource_id,
                    event.status,
                    serialized,
                ),
            )
        return event.model_copy(update={"project_id": project_id, "details": details})

    def list_execution_events(
        self,
        execution_id: str,
        project_id: str,
        *,
        expected_workflow_id: Optional[str],
        trust_execution_link: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Fetch lifecycle events bound to this execution, preserving the durable project scope."""
        project_detail = "(CASE WHEN json_valid(details_json) THEN json_extract(details_json, '$.project_id') END)"
        workflow_clause = ""
        values: list[Any] = [execution_id, project_id, project_id]
        if not trust_execution_link:
            if expected_workflow_id is None:
                workflow_clause = " AND workflow_id IS NULL"
            else:
                workflow_clause = " AND (workflow_id IS NULL OR workflow_id = ?)"
                values.append(expected_workflow_id)
        values.append(max(1, min(limit, 500)))
        with closing(self._database.connect()) as connection:
            execution = connection.execute(
                "SELECT project_id FROM execution_records WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if execution is None or execution["project_id"] != project_id:
                return []
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE execution_id = ? "
                "AND (project_id IS NULL OR project_id = ?) "
                f"AND ({project_detail} IS NULL OR {project_detail} = ?)"
                + workflow_clause
                + " ORDER BY created_at DESC, event_id DESC LIMIT ?",
                values,
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            details = _safe_details(row["details_json"])
            mismatched_workflow = row["workflow_id"] not in {None, expected_workflow_id}
            events.append({
                "event_id": row["event_id"],
                "created_at": row["created_at"],
                "actor_id": row["actor_id"],
                "event_type": row["event_type"],
                "request_id": row["request_id"],
                "workflow_id": None if mismatched_workflow else row["workflow_id"],
                "decision_id": row["decision_id"],
                "execution_id": row["execution_id"],
                "resource_id": None if mismatched_workflow else row["resource_id"],
                "status": row["status"],
                "details": details,
            })
        return events

    def list_events(
        self,
        limit: int = 100,
        event_type: Optional[str] = None,
        workflow_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        project_ids: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, 500))
        clauses: list[str] = []
        values: list[Any] = []
        scope_cte = ""
        scope_values: list[str] = []
        if event_type:
            clauses.append("event_type = ?")
            values.append(event_type)
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            values.append(workflow_id)
        if execution_id is not None:
            clauses.append("execution_id = ?")
            values.append(execution_id)
        if project_ids is not None:
            scope_values = list(dict.fromkeys(project_ids))[:900]
            if scope_values:
                scope_cte = f"WITH project_scope(project_id) AS (VALUES {','.join('( ? )' for _ in scope_values)}) "
                clauses.append(project_audit_scope_predicate())
            else:
                clauses.append("1 = 0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._database.connect()) as connection:
            rows = connection.execute(
                f"{scope_cte}SELECT * FROM audit_events {where} ORDER BY created_at DESC, event_id DESC LIMIT ?",
                [*scope_values, *values, bounded],
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            events.append({
                "event_id": row["event_id"],
                "created_at": row["created_at"],
                "actor_id": row["actor_id"],
                "event_type": row["event_type"],
                "request_id": row["request_id"],
                "workflow_id": row["workflow_id"],
                "decision_id": row["decision_id"],
                "execution_id": row["execution_id"],
                "resource_id": row["resource_id"],
                "status": row["status"],
                "details": _safe_details(row["details_json"]),
            })
        return events
