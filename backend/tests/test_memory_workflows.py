from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from backend.app.config import Settings
from backend.app.context.project_context import ProjectContextManager
from backend.app.main import create_app
from backend.app.memory.manager import MemoryManager, MemoryValidationError
from backend.app.memory.models import MemoryScope
from backend.app.memory.store import MemoryStore
from backend.app.observability.audit import AuditLog
from backend.app.security.approval_gate import ApprovalGate, ApprovalStatus
from backend.app.security.policy_engine import PolicyEngine
from backend.app.storage.sqlite import SQLiteDatabase
from backend.app.tools.executor import ToolExecutor
from backend.app.tools.models import ExecutionMode, RiskLevel, ToolAuthorization
from backend.app.tools.registry import ToolDefinition, ToolRegistry
from backend.app.workflows.engine import WorkflowEngine, WorkflowError
from backend.app.workflows.models import WorkflowDefinition, WorkflowStatus
from backend.app.workflows.state import WorkflowStateError, WorkflowStateStore


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


async def handler(value: BaseModel, _context: object) -> BaseModel:
    return Output(value=value.value + 1)


def make_tool(tool_id: str = "test.add", risk: RiskLevel = RiskLevel.LOW, permission: str = "project:read") -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        description="Test-only deterministic action",
        input_model=Input,
        output_model=Output,
        handler=handler,
        risk_level=risk,
        required_permissions=frozenset({permission}),
        timeout_seconds=2,
        execution_mode=ExecutionMode.INLINE_TRUSTED,
    )


def make_system(tmp_path: Path, risk: RiskLevel = RiskLevel.LOW):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    settings = Settings(
        database_path=tmp_path / "laya.sqlite3",
        project_roots={"demo": project},
        allowed_permissions=frozenset({"project:read", "project:write"}),
        approval_token="x" * 32,
    )
    database = SQLiteDatabase(settings.resolved_database_path())
    audit = AuditLog(database)
    projects = ProjectContextManager(settings)
    registry = ToolRegistry()
    registry.register(make_tool(risk=risk, permission="project:write" if risk != RiskLevel.LOW else "project:read"))
    approvals = ApprovalGate(database, audit, 300)
    policy = PolicyEngine(settings, approvals)

    async def authorize(definition, params, context, fingerprint, approval_id=None, action_ref=None):
        return policy.authorize(definition, params, context, fingerprint, approval_id, action_ref)

    executor = ToolExecutor(registry, authorize)
    state = WorkflowStateStore(database, audit)
    engine = WorkflowEngine(settings, state, registry, projects, policy, approvals, executor)
    return settings, database, audit, projects, registry, approvals, policy, executor, state, engine


def test_memory_persistent_temporary_delete_and_project_isolation(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    settings = Settings(project_roots={"a": root_a, "b": root_b}, database_path=tmp_path / "memory.sqlite3")
    db = SQLiteDatabase(settings.resolved_database_path())
    audit = AuditLog(db)
    manager = MemoryManager(ProjectContextManager(settings), MemoryStore(db), audit)
    saved = manager.put("a", "preference", {"theme": "dark"}, MemoryScope.PERSISTENT)
    assert saved.scope == MemoryScope.PERSISTENT
    assert manager.get("a", "preference", MemoryScope.PERSISTENT).value == {"theme": "dark"}
    assert manager.get("b", "preference", MemoryScope.PERSISTENT) is None
    manager.put("a", "scratch", "temporary", MemoryScope.TEMPORARY)
    assert manager.get("a", "scratch", MemoryScope.TEMPORARY).value == "temporary"
    assert manager.delete("a", "preference", MemoryScope.PERSISTENT)
    assert manager.get("a", "preference", MemoryScope.PERSISTENT) is None


def test_memory_rejects_secrets_and_unconfigured_projects(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    settings = Settings(project_roots={"demo": root}, database_path=tmp_path / "memory.sqlite3")
    db = SQLiteDatabase(settings.resolved_database_path())
    manager = MemoryManager(ProjectContextManager(settings), MemoryStore(db), AuditLog(db))
    with pytest.raises(MemoryValidationError):
        manager.put("demo", "auth", "secret")
    with pytest.raises(MemoryValidationError):
        manager.put("demo", "config", {"api_key": "real-secret"})
    with pytest.raises(Exception):
        manager.put("unknown", "note", "x")


def test_workflow_definition_rejects_cycles_and_unknown_dependencies() -> None:
    with pytest.raises(Exception):
        WorkflowDefinition(steps=[
            {"step_id": "a", "tool_id": "test.add", "parameters": {"value": 1}, "depends_on": ["b"]},
            {"step_id": "b", "tool_id": "test.add", "parameters": {"value": 2}, "depends_on": ["a"]},
        ])
    with pytest.raises(Exception):
        WorkflowDefinition(steps=[{"step_id": "a", "tool_id": "test.add", "parameters": {"value": 1}, "depends_on": ["missing"]}])


def test_low_risk_workflow_executes_sequentially_and_persists(tmp_path: Path) -> None:
    async def run() -> None:
        settings, _db, _audit, _projects, _registry, _approvals, _policy, _executor, state, engine = make_system(tmp_path)
        record = engine.create(WorkflowDefinition(project_id="demo", steps=[
            {"step_id": "one", "tool_id": "test.add", "parameters": {"value": 1}},
            {"step_id": "two", "tool_id": "test.add", "parameters": {"value": 2}, "depends_on": ["one"]},
        ]))
        completed = await engine.run(record.workflow_id)
        assert completed.status == WorkflowStatus.COMPLETED
        assert completed.state["completed_steps"] == ["one", "two"]
        assert state.get(record.workflow_id).version > 1

    asyncio.run(run())


def test_high_risk_workflow_pauses_for_approval_then_resumes(tmp_path: Path) -> None:
    async def run() -> None:
        settings, _db, _audit, _projects, _registry, approvals, _policy, _executor, _state, engine = make_system(tmp_path, RiskLevel.HIGH)
        record = engine.create(WorkflowDefinition(project_id="demo", steps=[
            {"step_id": "write", "tool_id": "test.add", "parameters": {"value": 4}},
        ]))
        waiting = await engine.run(record.workflow_id)
        assert waiting.status == WorkflowStatus.AWAITING_APPROVAL
        approval_id = waiting.state["approval_requests"]["write"]
        assert approvals.get(approval_id).status == ApprovalStatus.PENDING
        approvals.approve(approval_id, "human")
        completed = await engine.resume(record.workflow_id, {"write": approval_id})
        assert completed.status == WorkflowStatus.COMPLETED
        assert completed.state["completed_steps"] == ["write"]

    asyncio.run(run())


def test_workflow_rejects_sensitive_values_and_unknown_tools(tmp_path: Path) -> None:
    _settings, _db, _audit, _projects, _registry, _approvals, _policy, _executor, _state, engine = make_system(tmp_path)
    with pytest.raises(WorkflowError):
        engine.create(WorkflowDefinition(project_id="demo", steps=[
            {"step_id": "bad", "tool_id": "test.add", "parameters": {"value": 1, "api_key": "secret"}},
        ]))
    with pytest.raises(WorkflowError):
        engine.create(WorkflowDefinition(project_id="demo", steps=[
            {"step_id": "bad", "tool_id": "model.invented", "parameters": {"value": 1}},
        ]))


def test_workflow_api_requires_operator_and_runs_a_workflow(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        project_roots={"demo": project},
        allowed_permissions=frozenset({"project:read"}),
        approval_token="w" * 32,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/workflows").status_code == 401
        token = {"X-Laya-Approval-Token": "w" * 32}
        response = client.post("/api/v1/workflows", headers=token, json={
            "run_immediately": True,
            "definition": {
                "project_id": "demo",
                "steps": [{"step_id": "read", "tool_id": "file.read", "parameters": {"path": "README.md"}}],
            },
        })
        assert response.status_code == 201
        assert response.json()["status"] == "failed"  # File does not exist; no implicit retry/fallback.
        listed = client.get("/api/v1/workflows", headers=token)
        assert listed.status_code == 200
        assert listed.json()[0]["workflow_id"] == response.json()["workflow_id"]


def test_pausing_workflow_awaiting_approval_preserves_approval_for_resume(tmp_path: Path) -> None:
    async def run() -> None:
        _settings, _db, _audit, _projects, _registry, approvals, _policy, _executor, state, engine = make_system(tmp_path, RiskLevel.HIGH)
        record = engine.create(WorkflowDefinition(project_id="demo", steps=[
            {"step_id": "write", "tool_id": "test.add", "parameters": {"value": 4}},
        ]))
        waiting = await engine.run(record.workflow_id)
        approval_id = waiting.state["approval_requests"]["write"]
        paused = await engine.pause(record.workflow_id)
        assert paused.status == WorkflowStatus.PAUSED
        assert paused.state["approval_requests"]["write"] == approval_id
        with pytest.raises(WorkflowError):
            await engine.run(record.workflow_id)
        approvals.approve(approval_id, "human")
        resumed = await engine.resume(record.workflow_id)
        assert resumed.status == WorkflowStatus.COMPLETED
        assert state.get(record.workflow_id).state["completed_steps"] == ["write"]

    asyncio.run(run())


def test_workflow_approval_api_returns_only_matching_fingerprint(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    token = "f" * 32
    settings = Settings(
        database_path=tmp_path / "workflow-approval.sqlite3",
        project_roots={"demo": project},
        allowed_permissions=frozenset({"project:write"}),
        approval_token=token,
    )
    app = create_app(settings)
    headers = {"X-Laya-Approval-Token": token}
    parameters = {"path": "notes.txt", "content": "approved", "expected_sha256": None}

    with TestClient(app) as client:
        created = client.post("/api/v1/workflows", headers=headers, json={
            "run_immediately": True,
            "definition": {
                "project_id": "demo",
                "steps": [{"step_id": "write", "tool_id": "file.replace", "parameters": parameters}],
            },
        })
        assert created.status_code == 201
        workflow = created.json()
        assert workflow["status"] == WorkflowStatus.AWAITING_APPROVAL.value
        workflow_id = workflow["workflow_id"]
        approval_id = workflow["state"]["approval_requests"]["write"]

        approval_response = client.post("/api/v1/approvals", headers=headers, json={
            "tool_id": "file.replace",
            "parameters": parameters,
            "reason": "Review workflow write",
            "project_id": "demo",
            "workflow_id": workflow_id,
        })
        assert approval_response.status_code == 201
        assert approval_response.json()["approval_id"] == approval_id

        record = app.state.workflow_engine.get(workflow_id)
        wrong_parameters = {"path": "other.txt", "content": "not approved", "expected_sha256": None}
        mismatched = app.state.approval_gate.request(
            app.state.tool_registry.get("file.replace"),
            wrong_parameters,
            requested_by="test",
            reason="Deliberately mismatched test approval",
            project_id="demo",
            workflow_id=workflow_id,
        )
        state = dict(record.state)
        state["approval_requests"] = {"write": mismatched.approval_id}
        app.state.workflow_store.checkpoint(
            workflow_id,
            state,
            WorkflowStatus.AWAITING_APPROVAL,
            record.version,
        )
        rejected = client.post("/api/v1/approvals", headers=headers, json={
            "tool_id": "file.replace",
            "parameters": parameters,
            "reason": "Review workflow write",
            "project_id": "demo",
            "workflow_id": workflow_id,
        })
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "workflow_approval_missing"


def test_memory_api_is_explicit_and_authenticated(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = Settings(database_path=tmp_path / "api.sqlite3", project_roots={"demo": project}, approval_token="m" * 32)
    with TestClient(create_app(settings)) as client:
        payload = {"project_id": "demo", "key": "note", "value": {"theme": "dark"}, "scope": "persistent"}
        assert client.post("/api/v1/memory", json=payload).status_code == 401
        token = {"X-Laya-Approval-Token": "m" * 32}
        saved = client.post("/api/v1/memory", headers=token, json=payload)
        assert saved.status_code == 200
        assert client.get("/api/v1/memory/demo/note", headers=token).json()["value"] == {"theme": "dark"}
        rejected = client.post("/api/v1/memory", headers=token, json={
            "project_id": "demo", "key": "notes", "value": {"password": "secret"}, "scope": "persistent"
        })
        assert rejected.status_code == 422
