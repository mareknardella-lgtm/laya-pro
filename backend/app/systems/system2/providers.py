"""System 2 provider protocol and NVIDIA AI provider implementation.

Strict boundaries:
- Plain text generation only
- No tools, no execution, no sandbox access, no policy authority
- Fail-closed error handling and credentials redaction
"""

from __future__ import annotations

import json
from typing import Any, Optional, Protocol, runtime_checkable

import httpx

from ...config import Settings
from .exceptions import (
    System2AuthError,
    System2ContractError,
    System2NotConfiguredError,
    System2RateLimitError,
    System2RuntimeError,
)
from .models import GenerationRequest


@runtime_checkable
class System2Provider(Protocol):
    """Protocol for System 2 plain-text generation providers."""

    @property
    def provider_id(self) -> str:
        ...

    @property
    def is_configured(self) -> bool:
        ...

    async def generate(self, request: GenerationRequest) -> dict[str, Any]:
        ...


class DefaultHttpProvider:
    """Default HTTP provider speaking Laya Pro's normalized plain-text schema."""

    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._settings = settings
        self._transport = transport

    @property
    def provider_id(self) -> str:
        return "default"

    @property
    def is_configured(self) -> bool:
        return self._settings.system2_generate_url is not None

    async def generate(self, request: GenerationRequest) -> dict[str, Any]:
        endpoint = self._settings.system2_generate_url
        if endpoint is None:
            raise System2NotConfiguredError("No verified loopback System 2 generation endpoint is configured")
        if len(request.prompt) > self._settings.system2_max_input_chars:
            raise System2ContractError("Prompt exceeds the configured System 2 input limit")
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.system2_timeout_seconds,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                async with client.stream("POST", endpoint, json=request.model_dump(mode="json")) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise System2RuntimeError(f"System 2 runtime returned HTTP {response.status_code}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self._settings.system2_max_output_bytes:
                            raise System2ContractError("System 2 response exceeded the configured output limit")
        except (System2RuntimeError, System2ContractError):
            raise
        except httpx.TimeoutException as exc:
            raise System2RuntimeError("System 2 generation timed out") from exc
        except httpx.HTTPError as exc:
            raise System2RuntimeError("System 2 generation request failed") from exc
        try:
            parsed = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise System2ContractError("System 2 response was not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise System2ContractError("System 2 response must be a JSON object")
        return parsed


class NvidiaAiProvider:
    """NVIDIA AI generation provider slot (OpenAI-compatible chat/completions API).

    Translates GenerationRequest into an OpenAI-compatible payload and maps the text
    response back into Laya Pro's GenerationResponse dict contract:
    { "request_id": ..., "text": ..., "engine": ... }

    System 2 remains strictly non-authoritative: plain text only, no tool calling.
    """

    DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._settings = settings
        self._transport = transport

    @property
    def provider_id(self) -> str:
        return "nvidia"

    @property
    def is_configured(self) -> bool:
        if not self._settings.nvidia_ai_enabled:
            return False
        if not self._settings.nvidia_ai_model:
            return False
        base_url = self._settings.nvidia_ai_base_url or self.DEFAULT_BASE_URL
        if not base_url:
            return False
        return True

    def _resolved_endpoint(self) -> str:
        base = (self._settings.nvidia_ai_base_url or self.DEFAULT_BASE_URL).rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    async def generate(self, request: GenerationRequest) -> dict[str, Any]:
        if not self._settings.nvidia_ai_enabled:
            raise System2NotConfiguredError("NVIDIA AI provider is disabled (NVIDIA_AI_ENABLED=false)")
        if not self._settings.nvidia_ai_model:
            raise System2NotConfiguredError("NVIDIA AI model is not configured (NVIDIA_AI_MODEL is unset)")
        if len(request.prompt) > self._settings.system2_max_input_chars:
            raise System2ContractError("Prompt exceeds the configured System 2 input limit")

        endpoint = self._resolved_endpoint()
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._settings.nvidia_ai_api_key:
            headers["Authorization"] = f"Bearer {self._settings.nvidia_ai_api_key}"

        messages = []
        if request.context:
            context_summary = json.dumps(request.context, default=str)
            messages.append({
                "role": "system",
                "content": f"Context: {context_summary}\nYou are a non-authoritative plain-text assistant. Respond with helpful plain text only."
            })
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": self._settings.nvidia_ai_model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1024,
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._settings.nvidia_ai_timeout_seconds,
                transport=self._transport,
                follow_redirects=False,
                headers=headers,
            ) as client:
                async with client.stream("POST", endpoint, json=payload) as response:
                    if response.status_code in {401, 403}:
                        raise System2AuthError(f"NVIDIA AI authentication failed: HTTP {response.status_code}")
                    if response.status_code == 429:
                        raise System2RateLimitError("NVIDIA AI rate limit or quota exceeded: HTTP 429")
                    if response.status_code < 200 or response.status_code >= 300:
                        raise System2RuntimeError(f"NVIDIA AI provider returned HTTP {response.status_code}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self._settings.system2_max_output_bytes:
                            raise System2ContractError("NVIDIA AI response exceeded configured output limit")
        except (System2RuntimeError, System2ContractError):
            raise
        except httpx.TimeoutException as exc:
            raise System2RuntimeError("NVIDIA AI generation request timed out") from exc
        except httpx.HTTPError as exc:
            raise System2RuntimeError("NVIDIA AI generation request failed") from exc

        try:
            parsed = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise System2ContractError("NVIDIA AI response was not valid JSON") from exc

        if not isinstance(parsed, dict):
            raise System2ContractError("NVIDIA AI response must be a JSON object")

        choices = parsed.get("choices")
        if not isinstance(choices, list) or len(choices) == 0:
            raise System2ContractError("NVIDIA AI response did not contain completion choices")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise System2ContractError("NVIDIA AI choice must be a JSON object")

        message = first_choice.get("message")
        if not isinstance(message, dict) or "content" not in message:
            raise System2ContractError("NVIDIA AI response missing message content")

        generated_text = message.get("content")
        if not isinstance(generated_text, str) or not generated_text.strip():
            raise System2ContractError("NVIDIA AI produced empty or non-string text")

        return {
            "request_id": request.request_id,
            "text": generated_text.strip(),
            "engine": f"nvidia/{self._settings.nvidia_ai_model}",
        }
