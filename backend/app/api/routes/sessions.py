"""Session and history endpoints."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query, Request

router = APIRouter(tags=["sessions"])


@router.get("/sessions")
async def list_sessions(
    request: Request, limit: int = Query(default=20, ge=1, le=200)
) -> Dict[str, Any]:
    container = request.app.state.container
    sessions = await container.history.list_sessions(limit)
    return {"count": len(sessions), "sessions": sessions}


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> Dict[str, Any]:
    container = request.app.state.container
    messages = await container.history.get_messages(session_id, limit, direction)
    return {"session_id": session_id, "count": len(messages), "messages": messages}


@router.get("/sessions/{session_id}/traces")
async def get_traces(
    session_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
) -> Dict[str, Any]:
    container = request.app.state.container
    traces = await container.history.get_traces(session_id, limit)
    return {"session_id": session_id, "count": len(traces), "traces": traces}