"""Optional plain-text generation adapter with no action or authorization authority."""

from __future__ import annotations

import time
from typing import Optional

from ...config import Settings
from ...observability.runtime import RuntimeObservation
from .client import System2Client
from .exceptions import System2ContractError
from .models import GenerationRequest, GenerationResponse


class System2Adapter:
    def __init__(self, settings: Settings, client: Optional[System2Client] = None, observation: Optional[RuntimeObservation] = None) -> None:
        self._settings = settings
        self._client = client or System2Client(settings)
        self._observation = observation or RuntimeObservation()

    @property
    def observation(self) -> RuntimeObservation:
        return self._observation

    @property
    def runtime_configured(self) -> bool:
        return self._client.is_configured

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self._observation.attempted()
        started = time.monotonic()
        try:
            raw = await self._client.generate(request)
            try:
                response = GenerationResponse.model_validate(raw)
            except Exception as exc:
                raise System2ContractError("System 2 response did not match the plain-text generation contract") from exc
            if response.request_id != request.request_id:
                raise System2ContractError("System 2 response request_id does not match the originating request")
        except Exception as exc:
            self._observation.failed(exc, int((time.monotonic() - started) * 1000))
            raise
        self._observation.succeeded(int((time.monotonic() - started) * 1000))
        return response
