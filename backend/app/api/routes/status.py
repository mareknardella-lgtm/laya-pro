"""Non-sensitive configuration status; no remote runtime health is implied."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/v1", tags=["status"])


class StatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str = "laya-pro"
    mode: str
    system1_configured: bool
    system1_runtime_verified: bool = False
    system2_configured: bool = False
    tool_execution_available: bool = False


@router.get("/status", response_model=StatusResponse)
def status(request: Request) -> StatusResponse:
    settings = request.app.state.settings
    system2_adapter = request.app.state.system2_adapter
    return StatusResponse(
        mode=settings.app_mode,
        system1_configured=request.app.state.laya_adapter.runtime_configured,
        system2_configured=system2_adapter.runtime_configured,
        tool_execution_available=(
            len(request.app.state.tool_registry) > 0
            and request.app.state.settings.allowed_permissions != frozenset()
        ),
    )
