"""HTTP client for Nemotron 3.5 Lightning over an OpenAI-compatible API."""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional

import httpx

from ...config import Settings
from .exceptions import NemotronContractError, NemotronUnavailable
from .models import GenerationRequest, RenderRole

# A misconfigured or overloaded instance sometimes returns the same fragment
# glued together ("Iellsellsellsells..."). Shipping that to a user is worse
# than a clear error, so it is detected and retried once.
_MAX_RUNAWAY_WORD = 60
_MAX_REPEATED_FRAGMENT = 5
_MAX_TOKEN_DOMINANCE = 0.35
_ECHO_WINDOW = 60


def is_degenerate(text: str, prompt: str = "") -> bool:
    """Cheap repetition check; deliberately conservative to avoid false alarms.

    Also catches the "instance is echoing the prompt" failure, where the model
    stops answering and starts reciting its own input back.
    """

    words = text.split()
    if not words:
        return False

    if any(len(word) > _MAX_RUNAWAY_WORD for word in words):
        return True

    if len(words) >= 40:
        token, hits = Counter(words).most_common(1)[0]
        if hits / len(words) > _MAX_TOKEN_DOMINANCE:
            return True

    if len(text) >= 120:
        fragment = text[:12]
        if text.count(fragment) >= _MAX_REPEATED_FRAGMENT:
            return True

    flat = " ".join(prompt.split())
    if len(flat) >= _ECHO_WINDOW and flat[_ECHO_WINDOW // 2:_ECHO_WINDOW] in text:
        return True

    return False


class NemotronClient:
    """Thin transport layer: it sends the request and returns the raw payload."""

    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def is_configured(self) -> bool:
        return self._settings.nemotron_configured

    async def generate(self, request: GenerationRequest) -> dict[str, Any]:
        payload = await self._request(request, request.temperature)

        if is_degenerate(payload.get("text") or "", request.prompt):
            retry_temperature = min(
                1.0, (request.temperature or self._settings.nemotron_temperature) + 0.4
            )
            payload = await self._request(request, retry_temperature)
            if is_degenerate(payload.get("text") or "", request.prompt):
                raise NemotronContractError(
                    "Nemotron returned a degenerate reply (repeated fragment or prompt echo); "
                    "this usually means the shared endpoint instance is unhealthy"
                )
        return payload

    async def _request(
        self, request: GenerationRequest, temperature: Optional[float]
    ) -> dict[str, Any]:
        if not self.is_configured:
            raise NemotronUnavailable(
                "Nemotron is not configured; set NEMOTRON_API_KEY to reach the fast tier"
            )

        body: dict[str, Any] = {
            "model": self._settings.nemotron_model,
            "messages": [
                {"role": "system", "content": _system_prompt(request.role)},
                {"role": "user", "content": request.prompt},
            ],
            "temperature": _first_set(
                temperature, self._settings.nemotron_temperature
            ),
            "max_tokens": _first_set(
                request.max_tokens, self._settings.nemotron_max_output_tokens
            ),
            "stream": False,
        }

        # Nemotron 3.5 Lightning is a reasoning model: left on, it spends
        # hundreds of tokens on a trace of thought and leaks it into `content`
        # (the answer literally starts with "Here's a thinking process:").
        # The fast tier wants the answer, not the trace.
        if not self._settings.nemotron_enable_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}

        headers = {
            "Authorization": f"Bearer {self._settings.nemotron_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"{self._settings.nemotron_base_url.rstrip('/')}/chat/completions"

        client = self._client or httpx.AsyncClient(timeout=self._settings.nemotron_timeout_seconds)
        owns_client = self._client is None
        try:
            response = await client.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise NemotronUnavailable(
                f"Nemotron timed out after {self._settings.nemotron_timeout_seconds:.0f}s. "
                f"Raise NEMOTRON_TIMEOUT_SECONDS or lower NEMOTRON_MAX_OUTPUT_TOKENS."
            ) from exc
        except httpx.HTTPError as exc:
            raise NemotronUnavailable(
                f"Nemotron transport failure ({type(exc).__name__}): {exc or 'no details'}"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise NemotronUnavailable(
                f"Nemotron returned HTTP {response.status_code}: {response.text[:300]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise NemotronContractError("Nemotron response was not valid JSON") from exc

        return _extract_completion(payload, request.request_id)


def _extract_completion(payload: dict[str, Any], request_id: str) -> dict[str, Any]:
    """Normalize the OpenAI-compatible envelope into our contract."""

    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise NemotronContractError(
            "Nemotron response did not contain a chat completion"
        ) from exc

    if not isinstance(text, str) or not text.strip():
        raise NemotronContractError("Nemotron returned an empty completion")

    return {
        "request_id": request_id,
        "text": text.strip(),
        "engine": str(payload.get("model") or "nemotron"),
    }


def _first_set(*candidates: Optional[Any]) -> Optional[Any]:
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None


def _system_prompt(role: RenderRole) -> str:
    if role is RenderRole.ANSWER:
        return (
            "You are the fast response layer of a hybrid assistant. A deep reasoning "
            "engine has already produced a plan. Execute the plan faithfully and "
            "write the final answer for the user in the language they used. "
            "Never mention the plan, the pipeline, or internal engine names. "
            "If the plan is incomplete, stay within it and say plainly what is "
            "missing."
        )
    if role is RenderRole.CRITIQUE:
        return (
            "You audit a draft answer against the requirements it must satisfy. "
            "Reply with plain text: list each concrete gap on its own line prefixed "
            "by 'GAP: '. If there is no gap, reply with exactly 'APPROVED'."
        )
    if role is RenderRole.EXTRACT:
        return (
            "Extract durable facts about the user worth remembering across sessions. "
            "Reply with one 'key: value' pair per line and nothing else. Write "
            "'NONE' if there is nothing durable. Skip small talk and transient state."
        )
    return (
        "You are the fast layer of a hybrid assistant. Answer the user directly, "
        "accurately and concisely, in the language they used."
    )