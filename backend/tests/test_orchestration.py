from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.context.project_context import ProjectContextManager, ProjectContextError
from backend.app.core.action_router import ActionRouter
from backend.app.core.decision_engine import DecisionEngine, DecisionLimitError
from backend.app.core.orchestrator import OrchestrationStatus, Orchestrator
from backend.app.main import create_app
from backend.app.systems.laya.adapter import LayaAdapter
from backend.app.systems.laya.client import LayaClient
from backend.app.systems.laya.models import DecisionKind, DecisionRequest


def make_orchestrator(settings: Settings, response_kind: str, **decision_fields: object) -> Orchestrator:
    async def handler(request: httpx.Request) -> httpx.Response:
        import json
        payload = json.loads(request.content)
        decision = {
            "kind": response_kind,
            "reason": "test-only stub decision",
            "request_id": payload["request_id"],
            **decision_fields,
        }
        return httpx.Response(200, json={
            "decision": decision,
            "engine": "laya-system-1-test-stub",
            "runtime_configured": True,
        })

    test_client = LayaClient(settings, httpx.MockTransport(handler))
    adapter = LayaAdapter(settings, test_client)
    engine = DecisionEngine(settings, adapter)
    return Orchestrator(ActionRouter(ProjectContextManager(settings), engine))


def test_clarification_does_not_execute_or_create_action() -> None:
    async def run() -> None:
        settings = Settings(laya_decision_url="http://127.0.0.1:8766/decision")
        orchestrator = make_orchestrator(settings, "CLARIFICATION_REQUIRED", missing_information=["which project"])
        result = await orchestrator.process(DecisionRequest(user_input="change something"))
        assert result.status == OrchestrationStatus.CLARIFICATION_REQUIRED
        assert result.execution_status == "not_executed"
        assert result.action_id is None

    asyncio.run(run())


def test_action_is_only_a_proposal_until_executor_and_policy_exist() -> None:
    async def run() -> None:
        settings = Settings(laya_decision_url="http://127.0.0.1:8766/decision")
        orchestrator = make_orchestrator(
            settings,
            "ACTION",
            action_id="file.read",
            parameters={"path": "README.md"},
        )
        result = await orchestrator.process(DecisionRequest(user_input="read the readme"))
        assert result.status == OrchestrationStatus.ACTION_PROPOSED
        assert result.execution_status == "awaiting_registry_and_policy"

    asyncio.run(run())


def test_project_must_be_explicitly_configured(tmp_path) -> None:
    manager = ProjectContextManager(Settings())
    with pytest.raises(ProjectContextError):
        manager.resolve("unconfigured")
    configured = ProjectContextManager(Settings(project_roots={"demo": tmp_path}))
    assert configured.resolve("demo").root == tmp_path.resolve()


def test_iteration_limit_rejects_next_decision() -> None:
    async def run() -> None:
        settings = Settings(
            laya_decision_url="http://127.0.0.1:8766/decision",
            max_iterations=1,
        )
        orchestrator = make_orchestrator(settings, "NO_ACTION")
        with pytest.raises(DecisionLimitError):
            await orchestrator.process(DecisionRequest(user_input="continue", iteration=1))

    asyncio.run(run())


def test_api_status_explicitly_reports_runtime_unverified() -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.get("/api/v1/status")
    assert response.status_code == 200
    assert response.json() == {
        "service": "laya-pro",
        "mode": "local",
        "system1_configured": False,
        "system1_runtime_verified": False,
        "system2_configured": False,
        "tool_execution_available": False,
    }


def test_orchestrator_end_to_end_action_lifecycle(tmp_path) -> None:
    async def run() -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / "hello.txt").write_text("Hello from Laya Pro!", encoding="utf-8")
        settings = Settings(
            database_path=tmp_path / "orch.sqlite3",
            project_roots={"demo": root},
            allowed_permissions=frozenset({"project:read", "project:write"}),
            laya_decision_url="http://127.0.0.1:8766/decision",
        )
        app = create_app(settings)
        # Update orchestrator's decision engine adapter to use mock client
        async def handler(request: httpx.Request) -> httpx.Response:
            import json
            payload = json.loads(request.content)
            decision = {
                "kind": "ACTION",
                "reason": "Read hello file",
                "action_id": "file.read",
                "parameters": {"path": "hello.txt"},
                "request_id": payload["request_id"],
            }
            return httpx.Response(200, json={
                "decision": decision,
                "engine": "laya-system-1-mock",
                "runtime_configured": True,
            })
        app.state.laya_adapter._client = LayaClient(settings, httpx.MockTransport(handler))
        
        # 1. Proposal without execution flag: proposal only
        req_proposal = DecisionRequest(user_input="read hello", project_id="demo", execute=False)
        res_proposal = await app.state.orchestrator.process(req_proposal)
        assert res_proposal.status == OrchestrationStatus.ACTION_PROPOSED
        assert res_proposal.execution_status == "awaiting_registry_and_policy"
        assert res_proposal.execution_result is None

        # 2. Execution requested: goes through policy, tool executor, sandbox, result validation and memory
        req_exec = DecisionRequest(user_input="read hello", project_id="demo", execute=True)
        res_exec = await app.state.orchestrator.process(req_exec)
        assert res_exec.status == OrchestrationStatus.ACTION_EXECUTED
        assert res_exec.execution_status == "succeeded"
        assert res_exec.execution_result is not None
        assert res_exec.execution_result["status"] == "succeeded"
        assert res_exec.execution_result["output"]["content"] == "Hello from Laya Pro!"

        # 3. Memory has been updated
        entry = app.state.memory_manager.get("demo", "last_action_file.read")
        assert entry is not None
        assert entry.value["status"] == "succeeded"

    asyncio.run(run())


def test_orchestrator_approval_required_pipeline(tmp_path) -> None:
    async def run() -> None:
        root = tmp_path / "project"
        root.mkdir()
        settings = Settings(
            database_path=tmp_path / "orch_appr.sqlite3",
            project_roots={"demo": root},
            allowed_permissions=frozenset({"project:read", "project:write"}),
            laya_decision_url="http://127.0.0.1:8766/decision",
        )
        app = create_app(settings)
        async def handler(request: httpx.Request) -> httpx.Response:
            import json
            payload = json.loads(request.content)
            decision = {
                "kind": "ACTION",
                "reason": "Replace notes file",
                "action_id": "file.replace",
                "parameters": {"path": "notes.txt", "content": "confidential", "expected_sha256": None},
                "request_id": payload["request_id"],
            }
            return httpx.Response(200, json={
                "decision": decision,
                "engine": "laya-system-1-mock",
                "runtime_configured": True,
            })
        app.state.laya_adapter._client = LayaClient(settings, httpx.MockTransport(handler))

        # 1. Execution without approval_id triggers APPROVAL_REQUIRED and creates pending approval
        req = DecisionRequest(user_input="write notes", project_id="demo", execute=True)
        res = await app.state.orchestrator.process(req)
        assert res.status == OrchestrationStatus.APPROVAL_REQUIRED
        assert res.execution_status == "approval_required"
        assert res.approval_id is not None

        # Verify pending approval in database
        approval = app.state.approval_gate.get(res.approval_id)
        assert approval.status.value == "pending"

        # 2. Approve it
        app.state.approval_gate.approve(res.approval_id, "human-operator")

        # 3. Re-run execution providing the approval_id
        req_with_appr = DecisionRequest(user_input="write notes", project_id="demo", execute=True, approval_id=res.approval_id)
        res_with_appr = await app.state.orchestrator.process(req_with_appr)
        assert res_with_appr.status == OrchestrationStatus.ACTION_EXECUTED
        assert res_with_appr.execution_status == "succeeded"
        assert (root / "notes.txt").read_text(encoding="utf-8") == "confidential"

    asyncio.run(run())
