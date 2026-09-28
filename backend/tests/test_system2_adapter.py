from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.systems.system2.adapter import System2Adapter
from backend.app.systems.system2.client import System2Client
from backend.app.systems.system2.exceptions import (
    System2ContractError,
    System2NotConfiguredError,
    System2RuntimeError,
)
from backend.app.systems.system2.models import GenerationRequest
from fastapi.testclient import TestClient


def test_system2_is_not_configured_by_default_and_has_no_fallback() -> None:
    async def run() -> None:
        with pytest.raises(System2NotConfiguredError):
            await System2Adapter(Settings()).generate(GenerationRequest(prompt="Explain the architecture"))

    asyncio.run(run())


def test_system2_accepts_plain_text_from_explicit_mock_transport() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(200, json={
            "request_id": payload["request_id"],
            "text": "A local explanation.",
            "engine": "system2-test-stub",
        })

    async def run() -> None:
        settings = Settings(system2_generate_url="http://127.0.0.1:8767/generate")
        client = System2Client(settings, httpx.MockTransport(handler))
        adapter = System2Adapter(settings, client)
        result = await adapter.generate(GenerationRequest(prompt="Explain the architecture"))
        assert result.text == "A local explanation."
        assert result.engine == "system2-test-stub"

    asyncio.run(run())


def test_system2_rejects_tool_call_fields_and_request_id_mismatch() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(200, json={
            "request_id": payload["request_id"],
            "text": "Use a tool",
            "engine": "stub",
            "tool_calls": [{"name": "file.replace", "arguments": {}}],
        })

    async def mismatch_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"request_id": "wrong", "text": "text", "engine": "stub"})

    async def run() -> None:
        settings = Settings(system2_generate_url="http://127.0.0.1:8767/generate")
        adapter = System2Adapter(settings, System2Client(settings, httpx.MockTransport(handler)))
        with pytest.raises(System2ContractError):
            await adapter.generate(GenerationRequest(prompt="Generate"))
        adapter = System2Adapter(settings, System2Client(settings, httpx.MockTransport(mismatch_handler)))
        with pytest.raises(System2ContractError):
            await adapter.generate(GenerationRequest(prompt="Generate"))

    asyncio.run(run())


def test_system2_client_bounds_input_and_output_and_maps_errors() -> None:
    async def oversized_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b" " * 2048)

    async def failed_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"secret": "not returned"})

    async def run() -> None:
        settings = Settings(
            system2_generate_url="http://127.0.0.1:8767/generate",
            system2_max_input_chars=3,
            system2_max_output_bytes=1024,
        )
        client = System2Client(settings, httpx.MockTransport(oversized_handler))
        with pytest.raises(System2ContractError):
            await client.generate(GenerationRequest(prompt="four"))
        with pytest.raises(System2ContractError):
            await client.generate(GenerationRequest(prompt="ok"))
        client = System2Client(
            Settings(system2_generate_url="http://127.0.0.1:8767/generate"),
            httpx.MockTransport(failed_handler),
        )
        with pytest.raises(System2RuntimeError):
            await client.generate(GenerationRequest(prompt="ok"))

    asyncio.run(run())


def test_system2_api_fails_closed_when_not_configured(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "system2.sqlite3")
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/system2/generate", json={"prompt": "hello"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "system2_not_configured"


def test_generation_request_rejects_oversized_prompt_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        GenerationRequest(prompt="x" * 12_001)
    with pytest.raises(ValidationError):
        GenerationRequest(prompt="hello", tools=["file.read"])


def test_nvidia_provider_configuration_and_disabled_by_default() -> None:
    settings = Settings()
    assert settings.nvidia_ai_enabled is False
    assert settings.nvidia_ai_provider is None
    assert settings.active_system2_provider == "default"

    # Enabled but model missing:
    settings_no_model = Settings(
        nvidia_ai_enabled=True,
        nvidia_ai_provider="nvidia",
        system2_provider="nvidia",
    )
    client_no_model = System2Client(settings_no_model)
    assert client_no_model.is_configured is False
    async def run_no_model() -> None:
        with pytest.raises(System2NotConfiguredError) as exc_info:
            await client_no_model.generate(GenerationRequest(prompt="test"))
        assert "NVIDIA_MODEL is unset" in str(exc_info.value)
    asyncio.run(run_no_model())


def test_nvidia_provider_selection_and_unsupported_rejection() -> None:
    with pytest.raises(ValidationError):
        Settings(system2_provider="unsupported_vendor")

    with pytest.raises(ValidationError):
        Settings(nvidia_ai_provider="invalid_nvidia_provider")

    settings = Settings(
        nvidia_ai_enabled=True,
        nvidia_ai_provider="nvidia",
        nvidia_ai_model="meta/llama-3.2-11b-vision-instruct",
        nvidia_ai_api_key="nvapi-test-key",
    )
    assert settings.active_system2_provider == "nvidia"
    client = System2Client(settings)
    assert client.selected_provider_id == "nvidia"
    assert client.is_configured is True


def test_nvidia_provider_generates_plain_text_with_mock_transport() -> None:
    captured_requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured_requests.append({
            "url": str(request.url),
            "headers": dict(request.headers),
            "body": body,
        })
        return httpx.Response(200, json={
            "id": "chatcmpl-test-123",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "This is an NVIDIA AI plain-text explanation.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "model": "meta/llama-3.2-11b-vision-instruct",
        })

    async def run() -> None:
        settings = Settings(
            nvidia_ai_enabled=True,
            nvidia_ai_provider="nvidia",
            nvidia_ai_model="meta/llama-3.2-11b-vision-instruct",
            nvidia_ai_api_key="nvapi-mock-secret",
            nvidia_ai_base_url="https://integrate.api.nvidia.com/v1",
        )
        client = System2Client(settings, transport=httpx.MockTransport(handler))
        adapter = System2Adapter(settings, client)
        result = await adapter.generate(GenerationRequest(
            prompt="Summarize the project architecture",
            context={"project_id": "demo"},
        ))
        assert result.text == "This is an NVIDIA AI plain-text explanation."
        assert result.engine == "nvidia/meta/llama-3.2-11b-vision-instruct"
        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
        assert req["headers"].get("authorization") == "Bearer nvapi-mock-secret"
        assert req["body"]["model"] == "meta/llama-3.2-11b-vision-instruct"
        assert req["body"]["messages"][-1]["content"] == "Summarize the project architecture"

    asyncio.run(run())


def test_nvidia_provider_authentication_failure_handling() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

    async def run() -> None:
        settings = Settings(
            nvidia_ai_enabled=True,
            nvidia_ai_provider="nvidia",
            nvidia_ai_model="meta/llama-3.2-11b-vision-instruct",
            nvidia_ai_api_key="invalid-key",
        )
        client = System2Client(settings, transport=httpx.MockTransport(handler))
        with pytest.raises(System2RuntimeError) as exc_info:
            await client.generate(GenerationRequest(prompt="hello"))
        assert "authentication failed" in str(exc_info.value).lower()

    asyncio.run(run())


def test_nvidia_provider_malformed_response_handling() -> None:
    async def empty_choices_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    async def run() -> None:
        settings = Settings(
            nvidia_ai_enabled=True,
            nvidia_ai_provider="nvidia",
            nvidia_ai_model="meta/llama-3.2-11b-vision-instruct",
         nvidia_ai_api_key="test-key"
            )
        client = System2Client(settings, transport=httpx.MockTransport(empty_choices_handler))
        with pytest.raises(System2ContractError):
            await client.generate(GenerationRequest(prompt="hello"))

    asyncio.run(run())

