"""Decision engine delegates operational decisions only to the configured System 1 adapter."""

from __future__ import annotations

from ..config import Settings
from ..systems.laya.adapter import LayaAdapter
from ..systems.laya.models import DecisionEnvelope, DecisionRequest


class DecisionLimitError(ValueError):
    """A request exceeds the configured decision-cycle iteration bound."""


class DecisionEngine:
    def __init__(self, settings: Settings, laya: LayaAdapter) -> None:
        self._settings = settings
        self._laya = laya

    async def decide(self, request: DecisionRequest) -> DecisionEnvelope:
        if request.iteration >= self._settings.max_iterations:
            raise DecisionLimitError("The configured decision iteration limit has been reached")
        return await self._laya.decide(request)
