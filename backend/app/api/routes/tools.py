"""Read-only tool registry discovery route."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...tools.models import ToolSpec
from ...tools.registry import ToolRegistry

router = APIRouter(prefix="/api/v1", tags=["tools"])


@router.get("/tools", response_model=list[ToolSpec])
def list_tools(request: Request) -> list[ToolSpec]:
    registry: ToolRegistry = request.app.state.tool_registry
    return registry.list_specs()
