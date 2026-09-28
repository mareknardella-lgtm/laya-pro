"""System 1 decision and chat routes; no tools execute in this phase."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...core.orchestrator import DecisionResult, Orchestrator
from ...systems.laya.models import DecisionRequest

router = APIRouter(prefix="/api/v1", tags=["decisions"])


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


@router.post("/decisions", response_model=DecisionResult)
async def decide(body: DecisionRequest, request: Request, orchestrator: Orchestrator = Depends(get_orchestrator)) -> DecisionResult:
    result = await orchestrator.process(body)
    try:
        request.app.state.activity_service.record_decision(result)
    except Exception:
        import logging
        logging.getLogger("laya.api").warning("Decision metadata could not be persisted")
    return result


@router.post("/chat", response_model=DecisionResult)
async def chat(body: DecisionRequest, request: Request, orchestrator: Orchestrator = Depends(get_orchestrator)) -> DecisionResult:
    """Route through System 1; System 2 text generation is a separate explicit API."""
    result = await orchestrator.process(body)
    try:
        request.app.state.activity_service.record_decision(result)
    except Exception:
        import logging
        logging.getLogger("laya.api").warning("Decision metadata could not be persisted")
    return result
