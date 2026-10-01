"""Fail-closed adapter around Nemotron 3.5 Lightning."""

from __future__ import annotations

import time
from typing import Optional

from ...config import Settings
from ...observability.runtime import RuntimeObservation
from .client import NemotronClient
from .exceptions import NemotronContractError
from .models import GenerationRequest, GenerationResponse, RenderRole


class NemotronAdapter:
    """Validates every reply against our contract; never fabricates one."""

    def __init__(
        self,
        settings: Settings,
        client: Optional[NemotronClient] = None,
        observation: Optional[RuntimeObservation] = None,
    ) -> None:
        self._settings = settings
        self._client = client or NemotronClient(settings)
        self._observation = observation or RuntimeObservation("nemotron")

    @property
    def observation(self) -> RuntimeObservation:
        return self._observation

    @property
    def runtime_configured(self) -> bool:
        return self._client.is_configured

    @property
    def model(self) -> str:
        return self._settings.nemotron_model

    @property
    def engine_label(self) -> str:
        """Short human-readable engine name, e.g. nemotron-3.5-lightning-30b-a3b."""

        return self._settings.nemotron_model.split("/")[-1]

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self._observation.attempted()
        started = time.monotonic()
        try:
            raw = await self._client.generate(request)
            try:
                response = GenerationResponse.model_validate(raw)
            except Exception as exc:
                raise NemotronContractError(
                    "Nemotron response did not match the generation contract"
                ) from exc
            if response.request_id != request.request_id:
                raise NemotronContractError(
                    "Nemotron response request_id does not match the originating request"
                )
        except Exception as exc:
            self._observation.failed(exc, int((time.monotonic() - started) * 1000))
            raise
        self._observation.succeeded(int((time.monotonic() - started) * 1000))
        return response

    async def render(
        self,
        prompt: str,
        role: RenderRole = RenderRole.DIRECT,
        context: Optional[dict] = None,
    ) -> str:
        """Convenience wrapper returning just the text."""

        request = GenerationRequest(prompt=prompt, role=role, context=context or {})
        response = await self.generate(request)
        return response.text