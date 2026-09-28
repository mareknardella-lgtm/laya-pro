"""Chat UI endpoints integrating the Hybrid Orchestrator."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ...chat.models import ChatRequest, ChatResponse
from ...chat.orchestrator import HybridOrchestrator
from ...systems.system2.adapter import System2Adapter
from ...config import get_settings

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


from ...chat.history import ChatHistoryManager
from ...chat.memory import ChatMemoryManager

def get_orchestrator(request: Request) -> HybridOrchestrator:
    settings = get_settings()
    system2 = System2Adapter(settings)
    # Instantiate managers using app state dependencies
    history = ChatHistoryManager(request.app.state.database.connect())
    memory = ChatMemoryManager(system2, request.app.state.memory_manager)
    
    return HybridOrchestrator(settings, system2, request.app.state.laya_adapter, history, memory)


class ChatModesResponse(BaseModel):
    modes: list[str]


@router.get("/modes", response_model=ChatModesResponse)
async def get_chat_modes() -> ChatModesResponse:
    """Returns available chat modes."""
    return ChatModesResponse(modes=["LOW", "MEDIUM", "HARD"])


@router.post("/message", response_model=ChatResponse)
async def send_chat_message(
    request: ChatRequest,
    orchestrator: HybridOrchestrator = Depends(get_orchestrator)
) -> ChatResponse:
    """Send a message to the Hybrid Orchestrator."""
    return await orchestrator.process_message(request)

class CreateSessionRequest(BaseModel):
    title: str

@router.get("/sessions")
async def list_sessions(request: Request):
    history = ChatHistoryManager(request.app.state.database.connect())
    return history.list_sessions()

@router.post("/sessions")
async def create_session(req: CreateSessionRequest, request: Request):
    history = ChatHistoryManager(request.app.state.database.connect())
    session_id = history.create_session(req.title)
    return {"session_id": session_id}

@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, request: Request):
    history = ChatHistoryManager(request.app.state.database.connect())
    return history.get_messages(session_id)

@router.get("/memory/global")
async def list_memories(request: Request):
    from ...memory.models import MemoryScope
    return request.app.state.memory_manager.list("__global__", scope=MemoryScope.PERSISTENT)

class CreateMemoryRequest(BaseModel):
    key: str
    value: str

@router.post("/memory/global")
async def create_memory(req: CreateMemoryRequest, request: Request):
    from ...memory.models import MemoryScope
    request.app.state.memory_manager.put("__global__", req.key, req.value, scope=MemoryScope.PERSISTENT)
    return {"status": "ok"}

@router.delete("/memory/global/{key}")
async def delete_memory(key: str, request: Request):
    from ...memory.models import MemoryScope
    request.app.state.memory_manager.delete("__global__", key, scope=MemoryScope.PERSISTENT)
    return {"status": "ok"}
