"""Live end-to-end verification of both real AI systems in Laya Pro:
- Real local Laya System 1 service on loopback (http://127.0.0.1:8000/v1/systemone)
- Real NVIDIA AI System 2 service on NVIDIA Cloud API (https://integrate.api.nvidia.com/v1)
- Full orchestrator action lifecycle with safety controls
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import Settings
from backend.app.core.orchestrator import OrchestrationStatus
from backend.app.main import create_app
from backend.app.systems.laya.models import DecisionKind, DecisionRequest
from backend.app.systems.system2.models import GenerationRequest


async def main() -> None:
    print("=================================================================")
    print("  LAYA PRO — REAL AI SYSTEMS VERIFICATION (SYSTEM 1 & SYSTEM 2)  ")
    print("=================================================================\n")

    # 1. Start local Laya System 1 daemon
    venv_python = Path("./.venv-laya/Scripts/python.exe")
    runner_script = Path("scripts/run_local_laya.py")

    print("[1/4] Starting official local Laya System 1 daemon (http://127.0.0.1:8000)...")
    laya_proc = subprocess.Popen([str(venv_python), str(runner_script)])
    time.sleep(4)

    try:
        # Check health
        import httpx
        h = httpx.get("http://127.0.0.1:8000/health", timeout=5.0)
        print(f"      Laya Daemon Health: HTTP {h.status_code} -> {h.json()}")

        # 2. Configure Laya Pro with real local Laya endpoint and real NVIDIA API
        api_key_file = Path("C:/Users/Marek/Desktop/MIO/api key.txt")
        nvidia_key = None
        if api_key_file.is_file():
            for line in api_key_file.read_text(encoding="utf-8").splitlines():
                if "NVIDIA_NIM_API_KEY" in line and ("=" in line or ":" in line):
                    nvidia_key = line.split("=" if "=" in line else ":", 1)[1].strip()
                    break

        workspace_tmp = Path("backend/data/real_verify_tmp")
        workspace_tmp.mkdir(parents=True, exist_ok=True)
        doc = workspace_tmp / "overview.txt"
        doc.write_text("Laya Pro System 1 & System 2 fully connected.\n", encoding="utf-8")

        settings = Settings(
            database_path=Path("backend/data/real_verify.sqlite3"),
            project_roots={"verify": workspace_tmp.resolve()},
            allowed_permissions=frozenset({"project:read", "project:write"}),
            laya_decision_url="http://127.0.0.1:8000/v1/systemone",
            laya_timeout_seconds=30.0,
            nvidia_ai_enabled=True,
            nvidia_ai_provider="nvidia",
            nvidia_ai_model="meta/llama-3.2-11b-vision-instruct",
            nvidia_ai_api_key=nvidia_key,
            nvidia_ai_base_url="https://integrate.api.nvidia.com/v1",
            approval_token="live-verification-token-secret-12345",
        )
        app = create_app(settings)

        # 3. Test Real Local Laya AI System 1
        print("\n[2/4] Testing Real Local Laya AI System 1...")
        laya_req = DecisionRequest(
            user_input="Please read overview.txt",
            project_id="verify",
            execute=True,
        )
        laya_res = await app.state.orchestrator.process(laya_req)
        print(f"      Decision Kind: {laya_res.kind.value}")
        print(f"      Action ID:     {laya_res.action_id}")
        print(f"      Status:        {laya_res.status.value}")
        print(f"      Execution:     {laya_res.execution_status}")
        if laya_res.execution_result:
            content = laya_res.execution_result.get("output", {}).get("content", "")
            print(f"      File Read:     {content.strip()!r}")
        assert laya_res.status == OrchestrationStatus.ACTION_EXECUTED
        print("      -> SYSTEM 1 REAL LOCAL INFERENCE: SUCCESS")

        # 4. Test Real NVIDIA AI System 2
        print("\n[3/4] Testing Real NVIDIA AI System 2...")
        s2_req = GenerationRequest(prompt="Summarize in exactly 4 words: System 1 and System 2 online.")
        s2_res = await app.state.system2_adapter.generate(s2_req)
        print(f"      Response Text: {s2_res.text!r}")
        print(f"      Engine:        {s2_res.engine}")
        print("      -> SYSTEM 2 REAL INFERENCE: SUCCESS")

        # 5. Check Diagnostics
        print("\n[4/4] Verifying diagnostics endpoint...")
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            diag = client.get(
                "/api/v1/diagnostics",
                headers={"X-Laya-Approval-Token": "live-verification-token-secret-12345"}
            ).json()
            print(f"      System 1 Status: {diag['system1_laya']['status']} (operational: {diag['system1_laya']['operational']})")
            print(f"      System 2 Status: {diag['system2_generative']['status']} (operational: {diag['system2_generative']['operational']})")
            assert diag["system1_laya"]["operational"] is True
            assert diag["system2_generative"]["operational"] is True

        print("\n=================================================================")
        print("  ACCEPTANCE CHECK: PASS — BOTH REAL AI SYSTEMS OPERATIONAL!    ")
        print("=================================================================")

    finally:
        laya_proc.terminate()
        laya_proc.wait()
        print("\nLocal Laya daemon cleanly terminated.")


if __name__ == "__main__":
    asyncio.run(main())
