from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.systems.laya.adapter import LayaAdapter
from backend.app.systems.laya.client import LayaClient
from backend.app.systems.laya.exceptions import LayaContractError, LayaNotConfiguredError
from backend.app.systems.laya.models import DecisionKind, DecisionRequest, LayaDecision


def test_operational_decision_requires_id_and_parameters() -> None:
    with pytest.raises(ValidationError):
        LayaDecision(kind=DecisionKind.ACTION, reason="Do it", request_id="r1")


def test_clarification_requires_missing_information_and_cannot_have_action() -> None:
    with pytest.raises(ValidationError):
        LayaDecision(kind=DecisionKind.CLARIFICATION_REQUIRED, reason="Need details", request_id="r1")
    with pytest.raises(ValidationError):
        LayaDecision(
            kind=DecisionKind.CLARIFICATION_REQUIRED,
            reason="Need details",
            missing_information=["path"],
            action_id="file.read",
            parameters={"path": "x"},
            request_id="r1",
        )


def test_unconfigured_adapter_fails_without_fallback() -> None:
    async def run() -> None:
        adapter = LayaAdapter(Settings())
        with pytest.raises(LayaNotConfiguredError):
            await adapter.decide(DecisionRequest(user_input="hello"))

    asyncio.run(run())


def test_adapter_accepts_normalized_response_using_test_transport() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(200, json={
            "decision": {
                "kind": "NO_ACTION",
                "reason": "No tool operation is needed",
                "request_id": payload["request_id"],
            },
            "engine": "laya-system-1",
            "runtime_configured": True,
        })

    async def run() -> None:
        settings = Settings(laya_decision_url="http://127.0.0.1:8766/decision")
        client = LayaClient(settings, transport=httpx.MockTransport(handler))
        adapter = LayaAdapter(settings, client)
        result = await adapter.decide(DecisionRequest(user_input="hello"))
        assert result.decision.kind == DecisionKind.NO_ACTION
        assert result.runtime_configured is True

    asyncio.run(run())


def test_adapter_rejects_mismatched_request_id() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "decision": {"kind": "NO_ACTION", "reason": "Done", "request_id": "other"},
            "engine": "laya-system-1",
            "runtime_configured": True,
        })

    async def run() -> None:
        settings = Settings(laya_decision_url="http://127.0.0.1:8766/decision")
        adapter = LayaAdapter(settings, LayaClient(settings, httpx.MockTransport(handler)))
        with pytest.raises(LayaContractError):
            await adapter.decide(DecisionRequest(request_id="request-1", user_input="hello"))

    asyncio.run(run())


def test_client_rejects_oversized_output() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b" " * 2048)

    async def run() -> None:
        settings = Settings(
            laya_decision_url="http://127.0.0.1:8766/decision",
            laya_max_output_bytes=1024,
        )
        with pytest.raises(LayaContractError):
            await LayaClient(settings, httpx.MockTransport(handler)).decide(DecisionRequest(user_input="hello"))

    asyncio.run(run())


def test_client_sends_bearer_authorization_header_when_configured() -> None:
    received_headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        received_headers.update({k.lower(): v for k, v in request.headers.items()})
        payload = json.loads(request.content)
        return httpx.Response(200, json={
            "decision": {
                "kind": "NO_ACTION",
                "reason": "Authenticated request verified",
                "request_id": payload["request_id"],
            },
            "engine": "laya-system-1",
            "runtime_configured": True,
        })

    async def run() -> None:
        settings = Settings(
            laya_decision_url="http://127.0.0.1:8766/decision",
            laya_api_key="secret-token-12345",
        )
        client = LayaClient(settings, transport=httpx.MockTransport(handler))
        adapter = LayaAdapter(settings, client)
        result = await adapter.decide(DecisionRequest(user_input="authenticated test"))
        assert result.decision.kind == DecisionKind.NO_ACTION
        assert received_headers.get("authorization") == "Bearer secret-token-12345"

    asyncio.run(run())


def test_client_omits_authorization_header_when_no_api_key() -> None:
    received_headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        received_headers.update({k.lower(): v for k, v in request.headers.items()})
        payload = json.loads(request.content)
        return httpx.Response(200, json={
            "decision": {
                "kind": "NO_ACTION",
                "reason": "Unauthenticated request verified",
                "request_id": payload["request_id"],
            },
            "engine": "laya-system-1",
            "runtime_configured": True,
        })

    async def run() -> None:
        settings = Settings(laya_decision_url="http://127.0.0.1:8766/decision")
        client = LayaClient(settings, transport=httpx.MockTransport(handler))
        adapter = LayaAdapter(settings, client)
        result = await adapter.decide(DecisionRequest(user_input="unauthenticated test"))
        assert result.decision.kind == DecisionKind.NO_ACTION
        assert "authorization" not in received_headers

    asyncio.run(run())

