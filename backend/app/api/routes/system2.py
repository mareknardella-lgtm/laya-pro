"""Explicit System 2 generation API. It cannot propose or execute tool actions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...systems.system2.adapter import System2Adapter
from ...systems.system2.exceptions import System2AdapterError, System2NotConfiguredError
from ...systems.system2.models import GenerationRequest, GenerationResponse

router = APIRouter(prefix="/api/v1/system2", tags=["system2"])


def _adapter(request: Request) -> System2Adapter:
    return request.app.state.system2_adapter


@router.post("/generate", response_model=GenerationResponse)
async def generate(body: GenerationRequest, request: Request) -> GenerationResponse:
    adapter = _adapter(request)
    try:
        return await adapter.generate(body)
    except System2NotConfiguredError as exc:
        raise HTTPException(status_code=503, detail={"code": "system2_not_configured", "message": str(exc)}) from exc
    except System2AdapterError as exc:
        raise HTTPException(status_code=502, detail={"code": "system2_unavailable", "message": str(exc)}) from exc
