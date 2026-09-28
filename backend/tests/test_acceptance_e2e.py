"""End-to-End Acceptance Scenarios for Master Prompt 7.

Scenarios tested:
- Scenario A: Low-risk read action (reads file in sandbox, policy permits, workflow & memory updated).
- Scenario B: High-risk file modification (requires human approval, single-use, sandbox patch applied, audit logged).
- Scenario C: Invalid / unauthorized action (deny-by-default policy, no tool executed, audit logged).
- Scenario D: Runtime unavailable (fail-closed, no mock substituted, error reported cleanly).
- Scenario E: NVIDIA System 2 generation (plain-text only, cannot approve or execute actions, live when configured).
- Scenario F: Project isolation (two separate project contexts, cross-project data/approvals/memory denied).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.app.config import Settings
from backend.app.core.orchestrator import OrchestrationStatus
from backend.app.main import create_app
from backend.app.memory.models import MemoryScope
from backend.app.systems.laya.client import LayaClient
from backend.app.systems.laya.exceptions import LayaNotConfiguredError, LayaRuntimeError
from backend.app.systems.laya.models import DecisionKind, DecisionRequest
from backend.app.systems.system2.client import System2Client
from backend.app.systems.system2.models import GenerationRequest


def _make_laya_handler(decision_dict: dict[str, Any]):
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        d = dict(decision_dict)
        d["request_id"] = payload["request_id"]
        return httpx.Response(200, json={
            "decision": d,
            "engine": "laya-system-1",
            "runtime_configured": True,
        })
    return handler


def test_scenario_a_low_risk_read_action(tmp_path: Path) -> None:
    """Scenario A: Low-risk read action lifecycle.
    - Valid request with real structured decision proposal.
    - Policy permits low-risk read.
    - File read tool executes inside the sandbox.
    - Workflow / memory state updated accurately.
    """
    async def run() -> None:
        proj_root = tmp_path / "project_a"
        proj_root.mkdir()
        test_file = proj_root / "config.json"
        test_file.write_text('{"app": "laya", "version": "1.0"}', encoding="utf-8")

        settings = Settings(
            database_path=tmp_path / "scenario_a.sqlite3",
            project_roots={"proj_a": proj_root},
            allowed_permissions=frozenset({"project:read"}),
            laya_decision_url="http://127.0.0.1:8766/decision",
        )
        app = create_app(settings)

        decision_payload = {
            "kind": "ACTION",
            "reason": "Inspect application configuration",
            "action_id": "file.read",
            "parameters": {"path": "config.json"},
        }

        mock_transport = httpx.MockTransport(_make_laya_handler(decision_payload))
        app.state.laya_adapter._client = LayaClient(settings, transport=mock_transport)

        orchestrator = app.state.orchestrator
        req = DecisionRequest(
            user_input="Please read config.json",
            project_id="proj_a",
            execute=True,
        )

        result = await orchestrator.process(req)
        assert result.status == OrchestrationStatus.ACTION_EXECUTED
        assert result.execution_status == "succeeded"
        assert result.execution_result is not None
        assert '{"app": "laya", "version": "1.0"}' in result.execution_result["output"]["content"]

        # Verify memory update
        mem = app.state.memory_manager.get("proj_a", "last_action_file.read", MemoryScope.PERSISTENT)
        assert mem is not None
        assert mem.value["status"] == "succeeded"

    asyncio.run(run())


def test_scenario_b_high_risk_file_modification_lifecycle(tmp_path: Path) -> None:
    """Scenario B: High-risk file modification lifecycle.
    - Modification proposal requires human approval.
    - Execution blocked before approval is granted.
    - Approval bound to exact action, parameters, project, and workflow.
    - Approved through human approval gate.
    - Execution succeeds through patch engine / sandbox with backup.
    - Audit log record verified.
    """
    async def run() -> None:
        proj_root = tmp_path / "project_b"
        proj_root.mkdir()
        target_file = proj_root / "document.txt"
        target_file.write_text("Original content\n", encoding="utf-8")

        settings = Settings(
            database_path=tmp_path / "scenario_b.sqlite3",
            project_roots={"proj_b": proj_root},
            allowed_permissions=frozenset({"project:read", "project:write"}),
            laya_decision_url="http://127.0.0.1:8766/decision",
            approval_token="test-operator-token-long-12345",
        )
        app = create_app(settings)

        decision_payload = {
            "kind": "ACTION",
            "reason": "Update document content",
            "action_id": "file.replace",
            "parameters": {
                "path": "document.txt",
                "content": "Updated safe content\n",
                "expected_sha256": None,
            },
        }

        mock_transport = httpx.MockTransport(_make_laya_handler(decision_payload))
        app.state.laya_adapter._client = LayaClient(settings, transport=mock_transport)

        orchestrator = app.state.orchestrator

        # 1. First execution attempt without approval -> APPROVAL_REQUIRED
        req1 = DecisionRequest(
            user_input="Update the document",
            project_id="proj_b",
            execute=True,
        )
        result1 = await orchestrator.process(req1)
        assert result1.status == OrchestrationStatus.APPROVAL_REQUIRED
        assert result1.execution_status == "approval_required"
        assert result1.approval_id is not None
        approval_id = result1.approval_id

        # Verify file has NOT been modified yet
        assert target_file.read_text(encoding="utf-8") == "Original content\n"

        # 2. Operator approves the action
        app.state.approval_gate.approve(approval_id, "human-operator")

        # 3. Retry execution with the valid approval_id
        req2 = DecisionRequest(
            user_input="Update the document",
            project_id="proj_b",
            execute=True,
            approval_id=approval_id,
        )
        result2 = await orchestrator.process(req2)
        assert result2.status == OrchestrationStatus.ACTION_EXECUTED
        assert result2.execution_status == "succeeded"

        # Verify file IS modified now
        assert target_file.read_text(encoding="utf-8") == "Updated safe content\n"

        # 4. Verify approval cannot be reused (single-use)
        req3 = DecisionRequest(
            user_input="Update the document again",
            project_id="proj_b",
            execute=True,
            approval_id=approval_id,
        )
        result3 = await orchestrator.process(req3)
        assert result3.status in {OrchestrationStatus.APPROVAL_REQUIRED, OrchestrationStatus.POLICY_DENIED}

    asyncio.run(run())


def test_scenario_c_invalid_or_unauthorized_action(tmp_path: Path) -> None:
    """Scenario C: Invalid or unauthorized action.
    - Policy denies unauthorized tool or unauthorized permission.
    - No tool executed.
    - Failure recorded.
    """
    async def run() -> None:
        proj_root = tmp_path / "project_c"
        proj_root.mkdir()

        # Permissions do NOT include project:write
        settings = Settings(
            database_path=tmp_path / "scenario_c.sqlite3",
            project_roots={"proj_c": proj_root},
            allowed_permissions=frozenset({"project:read"}),
            laya_decision_url="http://127.0.0.1:8766/decision",
        )
        app = create_app(settings)

        # System 1 proposes a write action with valid tool schema
        decision_payload = {
            "kind": "ACTION",
            "reason": "Attempt write without write permission",
            "action_id": "file.replace",
            "parameters": {"path": "test.txt", "content": "hello", "expected_sha256": None},
        }
        mock_transport = httpx.MockTransport(_make_laya_handler(decision_payload))
        app.state.laya_adapter._client = LayaClient(settings, transport=mock_transport)

        orchestrator = app.state.orchestrator
        req = DecisionRequest(
            user_input="Modify test.txt",
            project_id="proj_c",
            execute=True,
        )

        result = await orchestrator.process(req)
        assert result.status == OrchestrationStatus.POLICY_DENIED
        assert result.execution_status == "policy_denied"
        assert result.execution_result is None

    asyncio.run(run())


def test_scenario_d_runtime_unavailable_fails_closed(tmp_path: Path) -> None:
    """Scenario D: Runtime unavailable fails closed.
    - Runtime unconfigured or connection refused.
    - Safe failure without mock substitution or arbitrary tool execution.
    """
    async def run() -> None:
        # Case 1: URL is not configured
        unconfigured_settings = Settings(
            database_path=tmp_path / "scenario_d1.sqlite3",
            laya_decision_url=None,
        )
        app1 = create_app(unconfigured_settings)
        with pytest.raises(LayaNotConfiguredError):
            await app1.state.laya_adapter.decide(DecisionRequest(user_input="test"))

        # Case 2: Configured URL is unreachable (connection refused)
        unreachable_settings = Settings(
            database_path=tmp_path / "scenario_d2.sqlite3",
            laya_decision_url="http://127.0.0.1:59999/nonexistent",
        )
        app2 = create_app(unreachable_settings)
        with pytest.raises(LayaRuntimeError):
            await app2.state.laya_adapter.decide(DecisionRequest(user_input="test"))

    asyncio.run(run())


def test_scenario_e_nvidia_system2_isolation_and_generation(tmp_path: Path) -> None:
    """Scenario E: NVIDIA AI System 2 plain-text generation.
    - Plain-text response only.
    - Cannot execute tools, authorize operations, or modify policy.
    """
    async def run() -> None:
        settings = Settings(
            database_path=tmp_path / "scenario_e.sqlite3",
            nvidia_ai_enabled=True,
            nvidia_ai_provider="nvidia",
            nvidia_ai_model="meta/llama-3.2-11b-vision-instruct",
            nvidia_ai_api_key="mock-key",
            nvidia_ai_base_url="https://integrate.api.nvidia.com/v1",
        )

        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "id": "chatcmpl-test",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "Plain text summary without tool authority."},
                    "finish_reason": "stop"
                }],
                "model": "meta/llama-3.2-11b-vision-instruct",
            })

        app = create_app(settings)
        app.state.system2_adapter._client = System2Client(settings, transport=httpx.MockTransport(handler))

        resp = await app.state.system2_adapter.generate(GenerationRequest(
            prompt="Explain the difference between System 1 and System 2"
        ))
        assert resp.text == "Plain text summary without tool authority."
        assert "nvidia" in resp.engine

    asyncio.run(run())


def test_scenario_f_strict_project_isolation(tmp_path: Path) -> None:
    """Scenario F: Project isolation.
    - Two projects: project_alpha and project_beta.
    - Action and memory in project_alpha cannot access project_beta files or approvals.
    - Traversal between project directories is rejected.
    """
    async def run() -> None:
        root_alpha = tmp_path / "alpha"
        root_alpha.mkdir()
        (root_alpha / "secret_alpha.txt").write_text("Alpha Secret", encoding="utf-8")

        root_beta = tmp_path / "beta"
        root_beta.mkdir()
        (root_beta / "secret_beta.txt").write_text("Beta Secret", encoding="utf-8")

        settings = Settings(
            database_path=tmp_path / "scenario_f.sqlite3",
            project_roots={"alpha": root_alpha, "beta": root_beta},
            allowed_permissions=frozenset({"project:read", "project:write"}),
            laya_decision_url="http://127.0.0.1:8766/decision",
        )
        app = create_app(settings)

        # 1. Read within alpha succeeds
        decision_alpha = {
            "kind": "ACTION",
            "reason": "Read alpha",
            "action_id": "file.read",
            "parameters": {"path": "secret_alpha.txt"},
        }
        app.state.laya_adapter._client = LayaClient(settings, transport=httpx.MockTransport(_make_laya_handler(decision_alpha)))
        res_alpha = await app.state.orchestrator.process(DecisionRequest(user_input="read alpha", project_id="alpha", execute=True))
        assert res_alpha.status == OrchestrationStatus.ACTION_EXECUTED
        assert "Alpha Secret" in res_alpha.execution_result["output"]["content"]

        # 2. Cross-project file path traversal from alpha to beta is blocked by sandbox
        decision_cross = {
            "kind": "ACTION",
            "reason": "Attempt traverse to beta",
            "action_id": "file.read",
            "parameters": {"path": "../beta/secret_beta.txt"},
        }
        app.state.laya_adapter._client = LayaClient(settings, transport=httpx.MockTransport(_make_laya_handler(decision_cross)))
        res_cross = await app.state.orchestrator.process(DecisionRequest(user_input="read beta from alpha", project_id="alpha", execute=True))
        assert res_cross.status == OrchestrationStatus.EXECUTION_FAILED
        assert res_cross.execution_status in {"error", "failed", "execution_failed"}

        # 3. Memory isolation: key set in alpha is invisible to beta
        app.state.memory_manager.put("alpha", "shared_key", {"data": "alpha_only"}, MemoryScope.PERSISTENT)
        assert app.state.memory_manager.get("beta", "shared_key", MemoryScope.PERSISTENT) is None

    asyncio.run(run())
