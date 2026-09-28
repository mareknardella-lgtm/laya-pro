from __future__ import annotations

import logging
from typing import Optional, Protocol, runtime_checkable

from .models import JevPlan
from ..config import Settings

logger = logging.getLogger("laya.chat.jev")

class JEVUnavailable(Exception):
    """Raised when the JEV System 1 engine is not configured or reachable."""
    pass


@runtime_checkable
class JevEngineProtocol(Protocol):
    """Abstract interface for the JEV System 1 Engine."""
    
    async def analyze_and_plan(self, message: str, context: Optional[dict] = None) -> JevPlan:
        """Perform structured analysis and return a concrete plan."""
        ...


class JevAdapter(JevEngineProtocol):
    """Adapter for the real JEV engine.
    
    Currently, the Ultra Jev repository is missing from the local environment,
    so this adapter immediately raises JEVUnavailable.
    
    To link a real implementation:
    1. Configure JEV_API_URL and JEV_API_KEY in .env.
    2. Implement the HTTP client here to POST to the JEV endpoint.
    3. Parse the JSON response into the JevPlan Pydantic model.
    """
    
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Example config lookup:
        # self._url = getattr(settings, "jev_api_url", None)

    async def analyze_and_plan(self, message: str, context: Optional[dict] = None) -> JevPlan:
        # In a real implementation, we would check if self._url is configured.
        # For now, it is explicitly not available.
        raise JEVUnavailable("Il motore JEV (System 1) non è configurato o accessibile localmente.")

