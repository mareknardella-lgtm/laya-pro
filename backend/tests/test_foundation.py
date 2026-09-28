from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.main import create_app


def test_health_endpoint_is_local_service_contract() -> None:
    async def run() -> None:
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "laya-pro", "mode": "local"}

    asyncio.run(run())


def test_public_bind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(host="0.0.0.0")


def test_remote_model_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(laya_decision_url="https://example.com/decide")


def test_laya_runtime_is_not_enabled_by_default() -> None:
    assert Settings().laya_decision_url is None
