"""Focused integration tests for real file modification acceptance and local Laya wrapper."""

from __future__ import annotations

import hashlib
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.systems.laya.local_bridge import LocalLayaBridge
from backend.app.systems.laya.models import DecisionRequest


@pytest.fixture
def acceptance_env(tmp_path: Path):
    test_dir = tmp_path / "laya-pro-acceptance-test"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_file = test_dir / "test_document.txt"
    initial_text = "Initial content for Laya Pro acceptance testing.\n"
    test_file.write_bytes(initial_text.encode("utf-8"))
    initial_sha = hashlib.sha256(initial_text.encode("utf-8")).hexdigest()

    token = "test-operator-token-32-bytes-long"
    settings = Settings(
        database_path=tmp_path / "test.sqlite3",
        project_roots={"acceptance": test_dir},
        allowed_permissions=frozenset({"project:read", "project:write"}),
        approval_token=token,
        laya_local_path=Path("C:/Users/Marek/Desktop/laya-coreml"),
        laya_python_path=Path("C:/Users/Marek/Desktop/laya-coreml/.venv/Scripts/python.exe"),
        laya_wrapper_path=Path("C:/Users/Marek/Desktop/laya-coreml/laya_torch.py"),
    )
    return {
        "settings": settings,
        "token": token,
        "test_dir": test_dir,
        "test_file": test_file,
        "initial_text": initial_text,
        "initial_sha": initial_sha,
    }


def test_real_file_modification_lifecycle(acceptance_env):
    """Verifies read -> unapproved block -> approve -> patch -> re-read -> sandbox path confinement."""
    settings = acceptance_env["settings"]
    token = acceptance_env["token"]
    test_file = acceptance_env["test_file"]
    initial_text = acceptance_env["initial_text"]
    initial_sha = acceptance_env["initial_sha"]

    app = create_app(settings)
    with TestClient(app) as client:
        # 1. Read test file through tool registry
        read_res = client.post(
            "/api/v1/files/read",
            headers={"X-Laya-Approval-Token": token},
            json={
                "project_id": "acceptance",
                "path": "test_document.txt",
                "idempotency_key": "step1-read",
            },
        )
        assert read_res.status_code == 200
        assert read_res.json()["output"]["content"] == initial_text
        assert read_res.json()["output"]["sha256"] == initial_sha

        # 2. Attempt replace without valid approval -> blocked
        new_line = "Modification verified through Laya Pro.\n"
        modified_content = initial_text + new_line
        replace_params = {
            "path": "test_document.txt",
            "content": modified_content,
            "expected_sha256": initial_sha,
        }

        unapproved = client.post(
            "/api/v1/files/replace",
            headers={"X-Laya-Approval-Token": token},
            json={
                "project_id": "acceptance",
                "path": "test_document.txt",
                "content": modified_content,
                "expected_sha256": initial_sha,
                "approval_id": "invalid-approval-id",
                "idempotency_key": "step2-blocked",
            },
        )
        assert unapproved.status_code in {403, 404}

        # 3. Create approval request
        appr_req = client.post(
            "/api/v1/approvals",
            headers={"X-Laya-Approval-Token": token},
            json={
                "tool_id": "file.replace",
                "parameters": replace_params,
                "project_id": "acceptance",
                "reason": "Test modification",
            },
        )
        assert appr_req.status_code == 201
        approval_id = appr_req.json()["approval_id"]

        # 4. Operator approves
        appr_dec = client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers={"X-Laya-Approval-Token": token},
        )
        assert appr_dec.status_code == 200

        # 5. Execute replace
        exec_res = client.post(
            "/api/v1/files/replace",
            headers={"X-Laya-Approval-Token": token},
            json={
                "project_id": "acceptance",
                "path": "test_document.txt",
                "content": modified_content,
                "expected_sha256": initial_sha,
                "approval_id": approval_id,
                "idempotency_key": "step5-replace",
            },
        )
        assert exec_res.status_code == 200
        assert exec_res.json()["output"]["status"] == "applied"

        # 6. Verify modification on disk and via read tool
        re_read = client.post(
            "/api/v1/files/read",
            headers={"X-Laya-Approval-Token": token},
            json={
                "project_id": "acceptance",
                "path": "test_document.txt",
                "idempotency_key": "step6-reread",
            },
        )
        assert re_read.status_code == 200
        assert re_read.json()["output"]["content"] == modified_content
        assert test_file.read_text(encoding="utf-8") == modified_content

        # 7. Verify sandbox path confinement
        traversal_read = client.post(
            "/api/v1/files/read",
            headers={"X-Laya-Approval-Token": token},
            json={
                "project_id": "acceptance",
                "path": "../test_document.txt",
                "idempotency_key": "step7-traversal",
            },
        )
        assert traversal_read.json()["status"] == "failed"
        assert "SandboxViolation" in traversal_read.json().get("error_message", "")


def test_local_laya_bridge_decision():
    """Verifies that LocalLayaBridge can invoke existing laya_torch and produce a typed decision."""
    import asyncio
    wrapper = Path("C:/Users/Marek/Desktop/laya-coreml/laya_torch.py")
    python_exe = Path("C:/Users/Marek/Desktop/laya-coreml/.venv/Scripts/python.exe")
    if not (wrapper.is_file() and python_exe.is_file()):
        pytest.skip("Local Laya installation not present on this machine")

    settings = Settings(
        laya_local_path=Path("C:/Users/Marek/Desktop/laya-coreml"),
        laya_python_path=python_exe,
        laya_wrapper_path=wrapper,
        laya_timeout_seconds=45.0,
    )
    bridge = LocalLayaBridge(settings)
    assert bridge.is_configured

    async def run():
        try:
            req = DecisionRequest(
                user_input="Read test_document.txt",
                project_id="acceptance",
            )
            res = await bridge.decide(req)
            assert "decision" in res
            assert res["decision"]["kind"] in {"ACTION", "NO_ACTION"}
            if res["decision"]["kind"] == "ACTION":
                assert res["decision"]["action_id"] in {"file.read", "file.replace"}
        finally:
            bridge.terminate()

    asyncio.run(run())
