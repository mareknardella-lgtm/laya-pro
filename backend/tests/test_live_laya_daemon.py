"""Real end-to-end integration test with live local laya-serve daemon and live NVIDIA AI."""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from backend.app.config import Settings
from backend.app.core.orchestrator import OrchestrationStatus
from backend.app.main import create_app
from backend.app.systems.laya.models import DecisionKind, DecisionRequest
from backend.app.systems.system2.models import GenerationRequest


def test_real_local_laya_daemon_inference_lifecycle(tmp_path: Path) -> None:
    """Spins up the official local laya.serve daemon and runs real live inference through Laya Pro."""
    venv_python = Path("./.venv-laya/Scripts/python.exe")
    if not venv_python.is_file():
        pytest.skip(".venv-laya environment not found")

    runner_script = Path("scripts/run_local_laya.py")
    if not runner_script.is_file():
        pytest.skip("scripts/run_local_laya.py not found")

    proc = subprocess.Popen([str(venv_python), str(runner_script)])
    time.sleep(4)

    try:
        # Check daemon health
        try:
            h = httpx.get("http://127.0.0.1:8000/health", timeout=5.0)
            assert h.status_code == 200
        except Exception as exc:
            pytest.fail(f"Could not reach local laya.serve health endpoint: {exc}")

        # Set up Laya Pro pointing to live local laya daemon
        proj_root = tmp_path / "project_real"
        proj_root.mkdir()
        (proj_root / "test_doc.txt").write_text("Real file content\n", encoding="utf-8")

        settings = Settings(
            database_path=tmp_path / "live_real.sqlite3",
            project_roots={"proj_real": proj_root},
            allowed_permissions=frozenset({"project:read", "project:write"}),
            laya_decision_url="http://127.0.0.1:8000/v1/systemone",
            laya_timeout_seconds=30.0,
            approval_token="test-operator-token-long-12345",
        )
        app = create_app(settings)

        async def run() -> None:
            # 1. Request real decision via LayaAdapter
            req = DecisionRequest(
                user_input="Please read file test_doc.txt",
                project_id="proj_real",
                execute=True,
            )
            env = await app.state.laya_adapter.decide(req)
            assert env.runtime_configured is True
            assert env.decision.request_id == req.request_id
            assert env.decision.kind == DecisionKind.ACTION
            assert env.decision.action_id == "file.read"
            assert "test_doc.txt" in env.decision.parameters.get("path", "")

            # 2. Process through orchestrator with real execution
            res = await app.state.orchestrator.process(req)
            assert res.status == OrchestrationStatus.ACTION_EXECUTED
            assert res.execution_status == "succeeded"
            assert res.execution_result is not None
            assert "Real file content" in res.execution_result["output"]["content"]

        asyncio.run(run())

    finally:
        proc.terminate()
        proc.wait()
