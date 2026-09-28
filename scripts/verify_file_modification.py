"""Master Prompt 11+12 Acceptance Verification Script.

Executes the full 12-step real filesystem modification acceptance test using:
- Existing local Laya CoreML/PyTorch installation (laya_torch.py on CPU)
- Existing NVIDIA AI System 2 integration (meta/llama-3.2-11b-vision-instruct)
- Dedicated acceptance workspace `laya-pro-acceptance-test/`
- Policy engine, single-use approval gate with HMAC token, sandbox confinement,
  and atomic patch engine with automatic backup.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.systems.laya.models import DecisionRequest
from backend.app.systems.system2.models import GenerationRequest


async def run_acceptance():
    print("==========================================================================")
    print("   LAYA PRO — MASTER PROMPT 11+12 REAL FILE MODIFICATION ACCEPTANCE TEST   ")
    print("==========================================================================\n")

    acceptance_dir = PROJECT_ROOT / "laya-pro-acceptance-test"
    target_file = acceptance_dir / "test_document.txt"

    assert acceptance_dir.is_dir(), f"Acceptance directory not found: {acceptance_dir}"
    assert target_file.is_file(), f"Target test file not found: {target_file}"

    # Read NVIDIA API Key from disk if present
    api_key_file = Path("C:/Users/Marek/Desktop/MIO/api key.txt")
    nvidia_key = None
    if api_key_file.is_file():
        for line in api_key_file.read_text(encoding="utf-8").splitlines():
            if "NVIDIA_NIM_API_KEY" in line and ("=" in line or ":" in line):
                nvidia_key = line.split("=" if "=" in line else ":", 1)[1].strip()
                break

    operator_token = "laya-pro-acceptance-operator-token-32b"

    settings = Settings(
        database_path=Path("backend/data/acceptance_test.sqlite3"),
        project_roots={"acceptance": acceptance_dir.resolve()},
        allowed_permissions=frozenset({"project:read", "project:write"}),
        approval_token=operator_token,
        # Local Laya existing installation:
        laya_local_path=Path("C:/Users/Marek/Desktop/laya-coreml"),
        laya_python_path=Path("C:/Users/Marek/Desktop/laya-coreml/.venv/Scripts/python.exe"),
        laya_wrapper_path=Path("C:/Users/Marek/Desktop/laya-coreml/laya_torch.py"),
        laya_timeout_seconds=45.0,
        # NVIDIA AI System 2:
        nvidia_ai_enabled=True,
        nvidia_ai_provider="nvidia",
        nvidia_ai_model="meta/llama-3.2-11b-vision-instruct",
        nvidia_ai_api_key=nvidia_key,
        nvidia_ai_base_url="https://integrate.api.nvidia.com/v1",
        nvidia_ai_timeout_seconds=30.0,
    )

    app = create_app(settings)

    print("[Step 1] Verifying System 1 (Local Laya) and FastAPI Backend initialization...")
    assert app.state.laya_adapter.runtime_configured is True
    print(f"         System 1 Configured: {app.state.laya_adapter.runtime_configured}")
    print(f"         Wrapper Path:        {settings.laya_wrapper_path}")

    # Test real local Laya inference
    laya_req = DecisionRequest(
        user_input="Read test_document.txt",
        project_id="acceptance",
    )
    laya_dec = await app.state.laya_adapter.decide(laya_req)
    print(f"         Laya Real Inference Output: kind={laya_dec.decision.kind.value}, action={laya_dec.decision.action_id}")
    print(f"         Calibrated Reason: {laya_dec.decision.reason}")
    assert laya_dec.decision.action_id == "file.read"

    print("\n[Step 2] Verifying System 2 (NVIDIA AI) availability...")
    assert app.state.system2_adapter.runtime_configured is True
    s2_req = GenerationRequest(prompt="Confirm in 4 words: NVIDIA AI System 2 connected.")
    s2_res = await app.state.system2_adapter.generate(s2_req)
    print(f"         NVIDIA AI Response: {s2_res.text.strip()!r}")
    print(f"         Engine:             {s2_res.engine}")

    print("\n[Step 3] Inspecting initial test file state...")
    initial_content = target_file.read_text(encoding="utf-8")
    initial_sha = hashlib.sha256(initial_content.encode("utf-8")).hexdigest()
    print(f"         Path:    {target_file}")
    print(f"         SHA-256: {initial_sha}")
    print(f"         Content: {initial_content.strip()!r}")
    assert "Initial content for Laya Pro acceptance testing." in initial_content

    print("\n[Step 4] Reading file through authorized tool registry (`file.read`)...")
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        read_res = client.post(
            "/api/v1/files/read",
            headers={"X-Laya-Approval-Token": operator_token},
            json={
                "project_id": "acceptance",
                "path": "test_document.txt",
                "idempotency_key": "step4-read-initial",
            },
        )
        assert read_res.status_code == 200, f"Read failed: {read_res.text}"
        read_body = read_res.json()
        print(f"         Status: HTTP {read_res.status_code}")
        print(f"         Tool Output Content: {read_body['output']['content'].strip()!r}")
        print(f"         Digest Match:        {read_body['output']['sha256'] == initial_sha}")
        assert read_body["output"]["content"] == initial_content

        print("\n[Step 5] Requesting controlled modification (append line)...")
        new_line = "Modification verified through Laya Pro.\n"
        requested_content = initial_content.rstrip("\n") + "\n" + new_line
        print(f"         Requested Appended Line: {new_line.strip()!r}")

        replace_params = {
            "path": "test_document.txt",
            "content": requested_content,
            "expected_sha256": initial_sha,
        }

        print("\n[Step 6] Testing policy enforcement and approval gate requirements...")
        # 6a. Attempt direct replacement without approval -> must fail
        unapproved = client.post(
            "/api/v1/files/replace",
            headers={"X-Laya-Approval-Token": operator_token},
            json={
                "project_id": "acceptance",
                "path": "test_document.txt",
                "content": requested_content,
                "expected_sha256": initial_sha,
                "approval_id": "non-existent-approval-id",
                "idempotency_key": "step6-unapproved-attempt",
            },
        )
        print(f"         Unapproved Execution Attempt: HTTP {unapproved.status_code} ({unapproved.json()['detail']['code']})")
        assert unapproved.status_code in {403, 404}

        # 6b. Create official approval request via /api/v1/approvals
        appr_req = client.post(
            "/api/v1/approvals",
            headers={"X-Laya-Approval-Token": operator_token},
            json={
                "tool_id": "file.replace",
                "parameters": replace_params,
                "project_id": "acceptance",
                "reason": "Master Prompt 11+12 Acceptance Test: append verified line to test_document.txt",
            },
        )
        assert appr_req.status_code == 201, f"Approval creation failed: {appr_req.text}"
        approval = appr_req.json()
        approval_id = approval["approval_id"]
        print(f"         Approval Created: ID={approval_id}, Status={approval['status']}")

        # 6c. Operator approves the action
        appr_dec = client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers={"X-Laya-Approval-Token": operator_token},
        )
        assert appr_dec.status_code == 200, f"Approve failed: {appr_dec.text}"
        print(f"         Approval Decided: Status={appr_dec.json()['status']}")

        print("\n[Step 7 & 8] Executing approved replacement via patch engine and sandbox...")
        exec_res = client.post(
            "/api/v1/files/replace",
            headers={"X-Laya-Approval-Token": operator_token},
            json={
                "project_id": "acceptance",
                "path": "test_document.txt",
                "content": requested_content,
                "expected_sha256": initial_sha,
                "approval_id": approval_id,
                "idempotency_key": "step7-approved-replace",
            },
        )
        assert exec_res.status_code == 200, f"Replacement execution failed: {exec_res.text}"
        exec_data = exec_res.json()
        print(f"         Execution Status: HTTP {exec_res.status_code}")
        print(f"         Operation ID:     {exec_data['output']['operation_id']}")
        print(f"         Backup ID:        {exec_data['output']['backup_id']}")
        print(f"         Bytes Written:    {exec_data['output']['bytes_written']}")

        print("\n[Step 9 & 10] Reading file again and verifying updated contents...")
        re_read = client.post(
            "/api/v1/files/read",
            headers={"X-Laya-Approval-Token": operator_token},
            json={
                "project_id": "acceptance",
                "path": "test_document.txt",
                "idempotency_key": "step9-re-read",
            },
        )
        assert re_read.status_code == 200
        final_read_content = re_read.json()["output"]["content"]
        disk_content = target_file.read_text(encoding="utf-8")
        assert final_read_content == disk_content
        assert final_read_content == requested_content
        print(f"         Final Read Content:\n{final_read_content}")
        print(f"         Expected Line Present: {'Modification verified through Laya Pro.' in final_read_content}")

        print("\n[Step 11] Verifying sandbox path confinement (traversal rejection)...")
        traversal_attempts = [
            "../test_document.txt",
            "..\\test_document.txt",
            "../../.env",
            "C:/Windows/notepad.exe",
        ]
        for bad_path in traversal_attempts:
            bad_res = client.post(
                "/api/v1/files/read",
                headers={"X-Laya-Approval-Token": operator_token},
                json={
                    "project_id": "acceptance",
                    "path": bad_path,
                    "idempotency_key": f"bad-path-{abs(hash(bad_path))}",
                },
            )
            # The tool execution either fails with SandboxViolation in output or raises HTTP error
            if bad_res.status_code == 200:
                res_body = bad_res.json()
                print(f"         Path Traversal '{bad_path}' -> Status {res_body['status']}, Error: {res_body.get('error_message')}")
                assert res_body["status"] == "failed"
                assert "SandboxViolation" in res_body.get("error_message", "")
            else:
                print(f"         Path Traversal '{bad_path}' -> HTTP {bad_res.status_code}")
                assert bad_res.status_code in {400, 422}

        print("\n[Step 12] Verifying no unrelated files were modified...")
        dir_files = list(acceptance_dir.iterdir())
        print(f"         Files in {acceptance_dir.name}: {[f.name for f in dir_files]}")
        assert len(dir_files) == 1
        assert dir_files[0].name == "test_document.txt"

    # Clean up bridge process
    if hasattr(app.state.laya_adapter, "_bridge") and app.state.laya_adapter._bridge:
        app.state.laya_adapter._bridge.terminate()

    print("\n==========================================================================")
    print("   ACCEPTANCE RESULT: PASS — REAL FILE MODIFICATION SAFELY VERIFIED!      ")
    print("==========================================================================")


if __name__ == "__main__":
    asyncio.run(run_acceptance())
