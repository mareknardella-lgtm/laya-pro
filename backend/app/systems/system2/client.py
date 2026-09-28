"""HTTP transport and provider selection for explicit System 2 generation requests.

Supports selectable providers:
- 'default': Direct HTTP loopback endpoint using Laya Pro normalized schema.
- 'nvidia': NVIDIA AI provider (local NIM loopback or NVIDIA Cloud API).

System 2 remains strictly non-authoritative: plain-text output only, no tool calling.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from ...config import Settings
from .exceptions import System2NotConfiguredError
from .models import GenerationRequest
from .providers import DefaultHttpProvider, NvidiaAiProvider, System2Provider


class System2Client:
    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._settings = settings
        self._transport = transport
        self._providers: dict[str, System2Provider] = {
            "default": DefaultHttpProvider(settings, transport=transport),
            "nvidia": NvidiaAiProvider(settings, transport=transport),
        }

    @property
    def selected_provider_id(self) -> str:
        return self._settings.active_system2_provider

    @property
    def is_configured(self) -> bool:
        provider = self._providers.get(self.selected_provider_id)
        return provider.is_configured if provider is not None else False

    def get_provider(self, provider_id: Optional[str] = None) -> System2Provider:
        pid = provider_id or self.selected_provider_id
        provider = self._providers.get(pid)
        if provider is None:
            raise System2NotConfiguredError(f"System 2 provider {pid!r} is not supported or not registered")
        return provider

    async def generate(self, request: GenerationRequest) -> dict[str, Any]:
        provider = self.get_provider()
        return await provider.generate(request)
