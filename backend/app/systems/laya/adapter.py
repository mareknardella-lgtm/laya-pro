"""Fail-closed System 1 adapter; it does not impersonate or emulate Laya."""

from __future__ import annotations

import time
from typing import Optional

from ...config import Settings
from ...observability.runtime import RuntimeObservation
from .client import LayaClient
from .exceptions import LayaContractError
from .local_bridge import LocalLayaBridge
from .models import DecisionEnvelope, DecisionRequest


class LayaAdapter:
    def __init__(
        self,
        settings: Settings,
        client: Optional[LayaClient] = None,
        bridge: Optional[LocalLayaBridge] = None,
        observation: Optional[RuntimeObservation] = None,
    ) -> None:
        self._settings = settings
        self._client = client or LayaClient(settings)
        self._bridge = bridge or (LocalLayaBridge(settings) if (settings.laya_python_path and settings.laya_wrapper_path) else None)
        self._observation = observation or RuntimeObservation()

    @property
    def observation(self) -> RuntimeObservation:
        return self._observation

    @property
    def runtime_configured(self) -> bool:
        if self._settings.laya_decision_url is not None:
            return True
        if self._bridge is not None and self._bridge.is_configured:
            return True
        return False

    async def decide(self, request: DecisionRequest) -> DecisionEnvelope:
        if len(request.user_input) > self._settings.laya_max_input_chars:
            error = LayaContractError("Input exceeds the configured Laya request limit")
            self._observation.attempted()
            self._observation.failed(error, 0)
            raise error
        self._observation.attempted()
        started = time.monotonic()
        try:
            if self._bridge is not None and self._bridge.is_configured and self._settings.laya_decision_url is None:
                raw = await self._bridge.decide(request)
            else:
                raw = await self._client.decide(request)
            try:
                decision = DecisionEnvelope.model_validate(raw)
            except Exception as exc:
                raise LayaContractError("Response did not match Laya Pro's normalized decision contract") from exc
            if decision.decision.request_id != request.request_id:
                raise LayaContractError("Decision request_id does not match the originating request")
            if not decision.runtime_configured:
                raise LayaContractError("A response cannot claim that the configured runtime is unavailable")
        except Exception as exc:
            self._observation.failed(exc, int((time.monotonic() - started) * 1000))
            raise
        self._observation.succeeded(int((time.monotonic() - started) * 1000))
        return decision
