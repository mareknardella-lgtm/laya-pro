"""Tests for the diagnostics endpoint and detailed AI systems observation."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.systems.laya.client import LayaClient
from backend.app.systems.laya.models import DecisionRequest
from backend.app.systems.system2.client import System2Client
from backend.app.systems.system2.models import GenerationRequest


def test_diagnostics_requires_operator_token(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "diag.sqlite3",
        approval_token="valid-secret-token-12345",
    )
    with TestClient(create_app(settings)) as client:
        # Without header -> 401
        res = client.get("/api/v1/diagnostics")
        assert res.status_code == 401

        # With valid header -> 200
        res = client.get("/api/v1/diagnostics", headers={"X-Laya-Approval-Token": "valid-secret-token-12345"})
        assert res.status_code == 200
        data = res.json()
        assert "system1_laya" in data
        assert "system2_generative" in data
        assert data["system1_laya"]["status"] == "not_configured"
        assert data["system2_generative"]["status"] == "not_configured"


def test_diagnostics_reports_auth_failed_and_operational_states(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "diag_states.sqlite3",
        approval_token="valid-secret-token-12345",
        laya_decision_url="http://127.0.0.1:8766/decision",
        nvidia_ai_enabled=True,
        nvidia_ai_provider="nvidia",
        nvidia_ai_model="meta/llama-3.2-11b-vision-instruct",
        nvidia_ai_api_key="bad-key",
        nvidia_ai_base_url="https://integrate.api.nvidia.com/v1",
    )
    app = create_app(settings)

    # 1. Unverified initially
    with TestClient(app) as client:
        headers = {"X-Laya-Approval-Token": "valid-secret-token-12345"}
        res = client.get("/api/v1/diagnostics", headers=headers)
        assert res.status_code == 200
        assert res.json()["system1_laya"]["status"] == "unverified"
        assert res.json()["system2_generative"]["status"] == "unverified"

    # 2. Simulate Laya Auth failure (HTTP 401)
    async def laya_401(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    app.state.laya_adapter._client = LayaClient(settings, transport=httpx.MockTransport(laya_401))
    import asyncio
    try:
        asyncio.run(app.state.laya_adapter.decide(DecisionRequest(user_input="hello")))
    except Exception:
        pass

    with TestClient(app) as client:
        res = client.get("/api/v1/diagnostics", headers=headers)
        assert res.status_code == 200
        assert res.json()["system1_laya"]["status"] == "auth_failed"
        assert res.json()["system1_laya"]["last_error_code"] == "LayaAuthError"

    # 3. Simulate System 2 rate limit / 429
    async def s2_429(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limit"})

    app.state.system2_adapter._client = System2Client(settings, transport=httpx.MockTransport(s2_429))
    try:
        asyncio.run(app.state.system2_adapter.generate(GenerationRequest(prompt="hello")))
    except Exception:
        pass

    with TestClient(app) as client:
        res = client.get("/api/v1/diagnostics", headers=headers)
        assert res.status_code == 200
        assert res.json()["system2_generative"]["status"] == "rate_limited"
        assert res.json()["system2_generative"]["last_error_code"] == "System2RateLimitError"

    # 4. Simulate successful operations -> operational state
    async def laya_200(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        return httpx.Response(200, json={
            "decision": {"kind": "NO_ACTION", "reason": "All good", "request_id": body["request_id"]},
            "engine": "laya-system-1",
            "runtime_configured": True,
        })

    async def s2_200(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "Fine."}}],
            "model": "meta/llama-3.2-11b-vision-instruct",
        })

    app.state.laya_adapter._client = LayaClient(settings, transport=httpx.MockTransport(laya_200))
    app.state.system2_adapter._client = System2Client(settings, transport=httpx.MockTransport(s2_200))

    asyncio.run(app.state.laya_adapter.decide(DecisionRequest(user_input="hello")))
    asyncio.run(app.state.system2_adapter.generate(GenerationRequest(prompt="hello")))

    with TestClient(app) as client:
        res = client.get("/api/v1/diagnostics", headers=headers)
        assert res.status_code == 200
        assert res.json()["system1_laya"]["status"] == "operational"
        assert res.json()["system1_laya"]["operational"] is True
        assert res.json()["system2_generative"]["status"] == "operational"
        assert res.json()["system2_generative"]["operational"] is True
