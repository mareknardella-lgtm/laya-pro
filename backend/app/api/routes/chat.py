"""Chat endpoint: the entry point of the hybrid pipeline."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...chat.models import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    container = request.app.state.container
    return await container.orchestrator.process_message(payload)