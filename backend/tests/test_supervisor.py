"""Automated test verifying process supervision, port readiness, and graceful shutdown."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx
import pytest


def test_supervisor_startup_and_graceful_shutdown() -> None:
    """Verifies that the PowerShell supervisor boots both services and shuts them down cleanly."""
    script_path = Path("scripts/run_backend.ps1")
    if not script_path.is_file():
        pytest.skip("scripts/run_backend.ps1 not found")

    test_laya_port = 8012
    test_backend_port = 8782

    # Launch supervisor with non-standard ports to avoid conflicts
    proc = subprocess.Popen([
        "powershell",
        "-ExecutionPolicy", "Bypass",
        "-File", str(script_path),
        "-LayaPort", str(test_laya_port),
        "-BackendPort", str(test_backend_port),
        "-TimeoutSeconds", "35",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    try:
        # Poll both ports for readiness
        laya_ready = False
        backend_ready = False

        sw_start = time.monotonic()
        while time.monotonic() - sw_start < 30:
            if not laya_ready:
                try:
                    r = httpx.get(f"http://127.0.0.1:{test_laya_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        laya_ready = True
                except Exception:
                    pass

            if not backend_ready:
                try:
                    r = httpx.get(f"http://127.0.0.1:{test_backend_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        backend_ready = True
                except Exception:
                    pass

            if laya_ready and backend_ready:
                break
            time.sleep(0.5)

        assert laya_ready, f"Laya service failed to become ready on port {test_laya_port}"
        assert backend_ready, f"FastAPI backend failed to become ready on port {test_backend_port}"

        # Wait for supervisor to write the PID file
        pid_file = Path("backend/data/supervisor_pids.json")
        sw_start = time.monotonic()
        while not pid_file.exists() and time.monotonic() - sw_start < 5:
            time.sleep(0.2)

    finally:
        # Invoke clean child teardown
        subprocess.run([
            "powershell",
            "-ExecutionPolicy", "Bypass",
            "-File", str(script_path),
            "-StopChildren"
        ], timeout=10)

        # Terminate supervisor process
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        # Wait for OS TCP socket recycling
        time.sleep(3)
        laya_closed = False
        backend_closed = False

        for _ in range(5):
            try:
                httpx.get(f"http://127.0.0.1:{test_laya_port}/health", timeout=1.0)
            except Exception:
                laya_closed = True
                break
            time.sleep(1)

        for _ in range(5):
            try:
                httpx.get(f"http://127.0.0.1:{test_backend_port}/health", timeout=1.0)
            except Exception:
                backend_closed = True
                break
            time.sleep(1)

        assert laya_closed, f"Laya port {test_laya_port} remained open after shutdown"
        assert backend_closed, f"Backend port {test_backend_port} remained open after shutdown"
