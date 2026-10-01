"""Fail-closed adapter around the laya-coreml reasoning runtime."""

from __future__ import annotations

import time
from typing import Optional

from ...config import Settings
from ...observability.runtime import RuntimeObservation
from .client import LayaClient
from .exceptions import LayaContractError
from .models import Critique, CritiqueRequest, ReasoningPlan, ReasoningRequest


class LayaAdapter:
    """Validates every reply into our plan and critique contracts.

    Nothing here can produce a plan on the runtime's behalf: if the runtime is
    down or breaks contract, the caller gets an error.
    """

    def __init__(
        self,
        settings: Settings,
        client: Optional[LayaClient] = None,
        observation: Optional[RuntimeObservation] = None,
    ) -> None:
        self._settings = settings
        self._client = client or LayaClient(settings)
        self._observation = observation or RuntimeObservation("laya-coreml")

    @property
    def observation(self) -> RuntimeObservation:
        return self._observation

    @property
    def runtime_configured(self) -> bool:
        return self._client.is_configured

    @property
    def engine_label(self) -> str:
        return self._settings.laya_engine_label

    async def reason(self, request: ReasoningRequest) -> ReasoningPlan:
        if len(request.problem) > self._settings.laya_max_input_chars:
            error = LayaContractError(
                f"Input exceeds the configured laya-coreml request limit "
                f"({self._settings.laya_max_input_chars} chars)"
            )
            self._observation.attempted()
            self._observation.failed(error, 0)
            raise error

        self._observation.attempted()
        started = time.monotonic()
        try:
            raw = await self._client.reason(request)
            try:
                plan = ReasoningPlan.model_validate(raw)
            except Exception as exc:
                raise LayaContractError(
                    "laya-coreml response did not match the reasoning plan contract"
                ) from exc
            if plan.request_id != request.request_id:
                raise LayaContractError(
                    "Reasoning plan request_id does not match the originating request"
                )
            if plan.depth is not request.depth:
                raise LayaContractError(
                    "Reasoning plan depth does not match the requested depth"
                )
        except Exception as exc:
            self._observation.failed(exc, int((time.monotonic() - started) * 1000))
            raise
        self._observation.succeeded(int((time.monotonic() - started) * 1000))
        return plan

    async def critique(self, request: CritiqueRequest) -> Critique:
        self._observation.attempted()
        started = time.monotonic()
        try:
            raw = await self._client.critique(request)
            try:
                critique = Critique.model_validate(raw)
            except Exception as exc:
                raise LayaContractError(
                    "laya-coreml response did not match the critique contract"
                ) from exc
            if critique.request_id != request.request_id:
                raise LayaContractError(
                    "Critique request_id does not match the originating request"
                )
        except Exception as exc:
            self._observation.failed(exc, int((time.monotonic() - started) * 1000))
            raise
        self._observation.succeeded(int((time.monotonic() - started) * 1000))
        return critique