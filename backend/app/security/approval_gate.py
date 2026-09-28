"""Persistent approval gate bound to an exact tool, parameters, context, and optional resource ID."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from ..observability.audit import AuditEvent, AuditLog
from ..storage.sqlite import SQLiteDatabase
from ..tools.fingerprint import input_fingerprint
from ..observability.safe_projection import safe_parameters
from ..tools.models import ToolAuthorization
from ..tools.registry import ToolDefinition


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class ApprovalError(ValueError):
    """Approval does not exist, is stale, or has already been decided/used."""


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    created_at: datetime
    expires_at: datetime
    status: ApprovalStatus
    tool_id: str
    input_fingerprint: str
    project_id: Optional[str]
    workflow_id: Optional[str]
    request_id: Optional[str]
    requested_by: str
    decided_by: Optional[str]
    reason: str
    parameters_preview: dict


class ApprovalGate:
    def __init__(self, database: SQLiteDatabase, audit: AuditLog, ttl_seconds: int = 900) -> None:
        self._database = database
        self._audit = audit
        if ttl_seconds < 30:
            raise ValueError("Approval TTL must be at least 30 seconds")
        self._ttl_seconds = ttl_seconds

    def request(
        self,
        definition: ToolDefinition,
        parameters: dict,
        requested_by: str,
        reason: str,
        project_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        request_id: Optional[str] = None,
        action_ref: Optional[str] = None,
    ) -> ApprovalRequest:
        validated = definition.input_model.model_validate(parameters)
        normalized = validated.model_dump(mode="json")
        fingerprint = self.fingerprint(definition, normalized, project_id, workflow_id, action_ref)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self._ttl_seconds)
        preview = safe_parameters(normalized)
        with self._database.transaction() as connection:
            existing = connection.execute(
                """SELECT * FROM approvals WHERE tool_id = ? AND input_fingerprint = ?
                AND status = 'pending' AND expires_at > ? ORDER BY created_at DESC LIMIT 1""",
                (definition.tool_id, fingerprint, now.isoformat()),
            ).fetchone()
            if existing:
                return self._from_row(existing)
            approval_id = str(uuid4())
            connection.execute(
                """INSERT INTO approvals (
                    approval_id, created_at, expires_at, status, tool_id, input_fingerprint,
                    project_id, workflow_id, request_id, parameters_preview_json, requested_by, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    approval_id,
                    now.isoformat(),
                    expires.isoformat(),
                    ApprovalStatus.PENDING.value,
                    definition.tool_id,
                    fingerprint,
                    project_id,
                    workflow_id,
                    request_id,
                    json.dumps(preview, ensure_ascii=False, sort_keys=True),
                    requested_by,
                    reason[:1_000],
                ),
            )
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        self._audit.record(AuditEvent(
            actor_id=requested_by,                    event_type="approval.requested",
                    request_id=request_id,
                    project_id=project_id,
                    workflow_id=workflow_id,

            resource_id=approval_id,
            status=ApprovalStatus.PENDING.value,
            details={"tool_id": definition.tool_id, "input_fingerprint": fingerprint, "project_id": project_id},
        ))
        return self._from_row(row)

    @staticmethod
    def fingerprint(
        definition: ToolDefinition,
        parameters: dict,
        project_id: Optional[str],
        workflow_id: Optional[str],
        action_ref: Optional[str] = None,
    ) -> str:
        normalized = definition.input_model.model_validate(parameters).model_dump(mode="json")
        scoped_parameters = normalized if action_ref is None else {"parameters": normalized, "action_ref": action_ref}
        return input_fingerprint(definition.tool_id, scoped_parameters, project_id, workflow_id)

    def get(self, approval_id: str) -> ApprovalRequest:
        with closing(self._database.connect()) as connection:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        if row is None:
            raise ApprovalError("Approval request does not exist")
        return self._from_row(row)

    def list_pending(self, limit: int = 100) -> list[ApprovalRequest]:
        bounded = max(1, min(limit, 500))
        now = datetime.now(timezone.utc).isoformat()
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE approvals SET status = ? WHERE status = ? AND expires_at <= ?",
                (ApprovalStatus.EXPIRED.value, ApprovalStatus.PENDING.value, now),
            )
            rows = connection.execute(
                "SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (ApprovalStatus.PENDING.value, bounded),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def approve(self, approval_id: str, approver_id: str) -> ApprovalRequest:
        return self._decide(approval_id, approver_id, ApprovalStatus.APPROVED)

    def reject(self, approval_id: str, approver_id: str) -> ApprovalRequest:
        return self._decide(approval_id, approver_id, ApprovalStatus.REJECTED)

    def _decide(self, approval_id: str, actor_id: str, target: ApprovalStatus) -> ApprovalRequest:
        now = datetime.now(timezone.utc)
        expired = False
        with self._database.transaction() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
            if row is None:
                raise ApprovalError("Approval request does not exist")
            if row["status"] != ApprovalStatus.PENDING.value:
                raise ApprovalError("Approval is no longer pending")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                connection.execute("UPDATE approvals SET status = ? WHERE approval_id = ?", (ApprovalStatus.EXPIRED.value, approval_id))
                expired = True
                updated = None
            else:
                cursor = connection.execute(
                    "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ? WHERE approval_id = ? AND status = ?",
                    (target.value, actor_id, now.isoformat(), approval_id, ApprovalStatus.PENDING.value),
                )
                if cursor.rowcount != 1:
                    raise ApprovalError("Approval was already decided")
                updated = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        if expired:
            raise ApprovalError("Approval has expired")
        self._audit.record(AuditEvent(
            actor_id=actor_id,
            event_type=f"approval.{target.value}",
            request_id=updated["request_id"],
            project_id=updated["project_id"],
            workflow_id=updated["workflow_id"],
            resource_id=approval_id,
            status=target.value,
            details={"tool_id": updated["tool_id"], "input_fingerprint": updated["input_fingerprint"]},
        ))
        return self._from_row(updated)

    def consume(
        self,
        approval_id: str,
        definition: ToolDefinition,
        fingerprint: str,
        project_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        parameters: Optional[dict] = None,
        action_ref: Optional[str] = None,
    ) -> ToolAuthorization:
        if parameters is None:
            raise ApprovalError("Validated parameters are required to consume an approval")
        expected = self.fingerprint(definition, parameters, project_id, workflow_id, action_ref)
        if expected != fingerprint:
            raise ApprovalError("Executor fingerprint and action parameters differ")
        now = datetime.now(timezone.utc)
        expired = False
        with self._database.transaction() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
            if row is None:
                raise ApprovalError("Approval request does not exist")
            if row["status"] != ApprovalStatus.APPROVED.value:
                raise ApprovalError("Approval is not in approved state")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                connection.execute("UPDATE approvals SET status = ? WHERE approval_id = ?", (ApprovalStatus.EXPIRED.value, approval_id))
                expired = True
                updated = None
            else:
                if (row["tool_id"] != definition.tool_id
                        or row["input_fingerprint"] != expected
                        or row["project_id"] != project_id
                        or row["workflow_id"] != workflow_id):
                    raise ApprovalError("Approval does not match the exact tool, parameters, and context")
                cursor = connection.execute(
                    "UPDATE approvals SET status = ? WHERE approval_id = ? AND status = ?",
                    (ApprovalStatus.CONSUMED.value, approval_id, ApprovalStatus.APPROVED.value),
                )
                if cursor.rowcount != 1:
                    raise ApprovalError("Approval was already consumed")
                updated = row
        if expired:
            raise ApprovalError("Approval has expired")
        self._audit.record(AuditEvent(
            actor_id="system:executor",
            event_type="approval.consumed",
            request_id=updated["request_id"],
            project_id=updated["project_id"],
            workflow_id=updated["workflow_id"],
            resource_id=approval_id,
            status=ApprovalStatus.CONSUMED.value,
            details={"tool_id": definition.tool_id},
        ))
        return ToolAuthorization(
            tool_id=definition.tool_id,
            input_fingerprint=fingerprint,
            permissions=definition.required_permissions,
            approval_id=approval_id,
            expires_at_epoch=datetime.fromisoformat(updated["expires_at"]).timestamp(),
        )

    @staticmethod
    def _from_row(row) -> ApprovalRequest:
        return ApprovalRequest(
            approval_id=row["approval_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            status=ApprovalStatus(row["status"]),
            tool_id=row["tool_id"],
            input_fingerprint=row["input_fingerprint"],
            project_id=row["project_id"],
            workflow_id=row["workflow_id"],
            request_id=row["request_id"],
            requested_by=row["requested_by"],
            decided_by=row["decided_by"],
            reason=row["reason"],
            parameters_preview=json.loads(row["parameters_preview_json"]),
        )


def _safe_preview(parameters: dict) -> dict:
    safe: dict = {}
    for key, value in parameters.items():
        name = str(key)[:128]
        normalized_name = name.lower().replace("-", "_").replace(" ", "_")
        if any(fragment in normalized_name for fragment in ("password", "secret", "token", "api_key", "credential", "auth")):
            safe[name] = "[REDACTED]"
        elif isinstance(value, dict):
            safe[name] = _safe_preview(value)
        elif isinstance(value, list):
            safe[name] = [_safe_preview({"item": item})["item"] for item in value[:32]]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[name] = value[:256] if isinstance(value, str) else value
        else:
            safe[name] = "[structured value omitted]"
    return safe
