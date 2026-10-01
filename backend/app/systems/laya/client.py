"""HTTP client for the local laya-coreml reasoning runtime.

The runtime speaks JSON in and JSON out. It never returns free prose: if it
cannot produce a plan it answers with an explicit refusal, which this layer
turns into an exception rather than an improvised plan.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from ...config import Settings
from .exceptions import LayaContractError, LayaRefusal, LayaUnavailable
from .models import CritiqueRequest, ReasoningRequest

_REFUSAL_MARKERS = ("refused", "cannot_plan", "insufficient_information")


class LayaClient:
    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def is_configured(self) -> bool:
        return self._settings.laya_configured

    async def reason(self, request: ReasoningRequest) -> dict[str, Any]:
        if not self.is_configured:
            raise LayaUnavailable(
                "laya-coreml is not configured; set LAYA_COREML_URL to reach the reasoning tier"
            )
        if len(request.problem) > self._settings.laya_max_input_chars:
            raise LayaUnavailable(
                f"Problem exceeds the {self._settings.laya_max_input_chars} character reasoning limit"
            )

        body = {
            "request_id": request.request_id,
            "problem": request.problem,
            "depth": request.depth.value,
            "intent": request.intent,
            "context": request.context,
        }
        payload = await self._post("/v1/reason", body, request.request_id)
        self._reject_if_refusal(payload, request.request_id)
        payload.setdefault("depth", request.depth.value)
        payload["request_id"] = request.request_id
        return payload

    async def critique(self, request: CritiqueRequest) -> dict[str, Any]:
        if not self.is_configured:
            raise LayaUnavailable("laya-coreml is not configured")

        body = {
            "request_id": request.request_id,
            "draft": request.draft,
            "plan": request.plan.model_dump(),
            "requirements": request.requirements,
        }
        payload = await self._post("/v1/critique", body, request.request_id)
        payload["request_id"] = request.request_id
        payload.setdefault("verdict", "revisions_required")
        return payload

    async def _post(self, path: str, body: dict[str, Any], request_id: str) -> dict[str, Any]:
        url = f"{self._settings.laya_coreml_url.rstrip('/')}{path}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._settings.laya_coreml_api_key:
            headers["Authorization"] = f"Bearer {self._settings.laya_coreml_api_key}"

        client = self._client or httpx.AsyncClient(timeout=self._settings.laya_coreml_timeout_seconds)
        owns_client = self._client is None
        try:
            response = await client.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise LayaUnavailable(
                f"laya-coreml timed out after {self._settings.laya_coreml_timeout_seconds:.0f}s. "
                f"Raise LAYA_COREML_TIMEOUT_SECONDS."
            ) from exc
        except httpx.HTTPError as exc:
            raise LayaUnavailable(
                f"laya-coreml transport failure ({type(exc).__name__}): {exc or 'no details'}"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise LayaUnavailable(
                f"laya-coreml returned HTTP {response.status_code}: {response.text[:300]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise LayaContractError("laya-coreml response was not valid JSON") from exc

        if not isinstance(payload, dict):
            raise LayaContractError("laya-coreml response was not a JSON object")
        return payload

    @staticmethod
    def _reject_if_refusal(payload: dict[str, Any], request_id: str) -> None:
        """A refusal must never be silently replaced by an invented plan."""

        for marker in _REFUSAL_MARKERS:
            detail = payload.get(marker)
            if detail:
                raise LayaRefusal(
                    "laya-coreml refused to produce a reasoning plan",
                    reason=str(detail)[:500],
                )