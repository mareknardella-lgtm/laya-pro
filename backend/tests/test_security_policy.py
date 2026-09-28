from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.observability.audit import AuditEvent, AuditLog
from backend.app.security.approval_gate import ApprovalError, ApprovalGate, ApprovalStatus
from backend.app.tools.executor import input_fingerprint
from backend.app.security.policy_engine import ApprovalRequiredError, PolicyEngine
from backend.app.storage.sqlite import SQLiteDatabase
from backend.app.tools.fingerprint import input_fingerprint
from backend.app.tools.models import ExecutionMode, RiskLevel
from backend.app.tools.registry import ToolDefinition, ToolRegistry


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    token: Optional[str] = None


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


async def handler(_params: BaseModel, _context: object) -> BaseModel:
    return Output(value="ok")


def make_definition(risk: RiskLevel = RiskLevel.HIGH) -> ToolDefinition:
    return ToolDefinition(
        tool_id="test.write",
        description="Test only",
        input_model=Parameters,
        output_model=Output,
        handler=handler,
        risk_level=risk,
        required_permissions=frozenset({"project:write"}),
        timeout_seconds=1,
        execution_mode=ExecutionMode.INLINE_TRUSTED,
    )


def test_policy_defaults_to_deny_and_requires_project_scope() -> None:
    definition = make_definition(RiskLevel.LOW)
    denied = PolicyEngine(Settings(allowed_permissions=frozenset())).evaluate(definition, {"path": "x"})
    assert not denied.allowed
    allowed_permission_no_project = PolicyEngine(Settings(allowed_permissions=frozenset({"project:write"}))).evaluate(
        definition, {"path": "x"}
    )
    assert not allowed_permission_no_project.allowed


def test_high_risk_policy_requires_human_approval(tmp_path) -> None:
    from backend.app.context.project_context import ProjectContext
    from backend.app.core.execution_context import ExecutionContext
    from backend.app.context.workflow_context import WorkflowContext

    project = ProjectContext(project_id="demo", root=tmp_path)
    context = ExecutionContext(
        request_id="request",
        project=project,
        workflow=WorkflowContext(workflow_id=None, project_id="demo", iteration=0),
    )
    policy = PolicyEngine(Settings(allowed_permissions=frozenset({"project:write"})))
    with pytest.raises(ApprovalRequiredError):
        policy.authorize(make_definition(), {"path": "file.txt"}, context, input_fingerprint("test.write", {"path": "file.txt", "token": None}, "demo", None))


def test_approval_is_expiring_single_use_context_and_parameter_bound(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "laya.sqlite3")
    audit = AuditLog(db)
    gate = ApprovalGate(db, audit, ttl_seconds=300)
    definition = make_definition()
    parameters = {"path": "allowed.txt", "token": "do-not-log"}
    approval = gate.request(
        definition,
        parameters,
        requested_by="test",
        reason="test approval",
        project_id="demo",
        workflow_id="wf-1",
        request_id="req-1",
    )
    assert approval.status == ApprovalStatus.PENDING
    assert approval.parameters_preview["token"] == "[REDACTED]"
    gate.approve(approval.approval_id, "human")
    correct = ApprovalGate.fingerprint(definition, parameters, "demo", "wf-1")
    capability = gate.consume(approval.approval_id, definition, correct, "demo", "wf-1", parameters)
    assert capability.approval_id == approval.approval_id
    with pytest.raises(ApprovalError):
        gate.consume(approval.approval_id, definition, correct, "demo", "wf-1", parameters)


def test_approval_rejects_changed_parameters_and_context(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "laya.sqlite3")
    gate = ApprovalGate(db, AuditLog(db))
    definition = make_definition()
    approval = gate.request(definition, {"path": "one"}, "system", "reason", project_id="demo")
    gate.approve(approval.approval_id, "human")
    changed = ApprovalGate.fingerprint(definition, {"path": "two", "token": None}, "demo", None)
    with pytest.raises(ApprovalError):
        gate.consume(approval.approval_id, definition, changed, "demo", None, {"path": "two"})


def test_audit_redacts_secret_fields_and_does_not_store_raw_secrets(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "laya.sqlite3")
    audit = AuditLog(db)
    saved = audit.record(AuditEvent(
        actor_id="test",
        event_type="test.event",
        status="ok",
        details={"api_key": "very-secret", "nested": {"password": "also-secret", "count": 2}},
    ))
    assert saved.details["api_key"] == "[REDACTED]"
    rows = audit.list_events()
    assert rows[0]["details"]["nested"]["password"] == "[REDACTED]"
    assert "very-secret" not in str(rows)


def test_approval_and_audit_api_require_operator_token(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.sqlite3")
    registry = ToolRegistry()
    registry.register(make_definition())
    app = create_app(settings)
    app.state.tool_registry = registry
    with TestClient(app) as client:
        assert client.get("/api/v1/approvals").status_code == 503
        assert client.get("/api/v1/audit").status_code == 503

    secured = Settings(database_path=tmp_path / "api-secure.sqlite3", approval_token="a" * 32)
    secured_app = create_app(secured)
    secured_app.state.tool_registry = registry
    with TestClient(secured_app) as client:
        assert client.get("/api/v1/approvals", headers={"X-Laya-Approval-Token": "wrong"}).status_code == 401
        assert client.get("/api/v1/audit", headers={"X-Laya-Approval-Token": "a" * 32}).status_code == 200


def test_approval_api_returns_safe_preview_and_requires_explicit_approval(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        allowed_permissions=frozenset({"project:write"}),
        project_roots={"demo": tmp_path},
        approval_token="b" * 32,
    )
    app = create_app(settings)
    app.state.tool_registry.register(make_definition())
    with TestClient(app) as client:
        created = client.post("/api/v1/approvals", headers={"X-Laya-Approval-Token": "b" * 32}, json={
            "tool_id": "test.write",
            "parameters": {"path": "file.txt", "token": "do-not-show"},
            "reason": "Unit test",
            "project_id": "demo",
        })
        assert created.status_code == 201
        body = created.json()
        assert body["status"] == "pending"
        assert body["parameters_preview"]["token"] == "[REDACTED]"
        token = {"X-Laya-Approval-Token": "b" * 32}
        decided = client.post(f"/api/v1/approvals/{body['approval_id']}/approve", headers=token)
        assert decided.status_code == 200
        assert decided.json()["status"] == "approved"
        duplicate = client.post(f"/api/v1/approvals/{body['approval_id']}/approve", headers=token)
        assert duplicate.status_code == 409
