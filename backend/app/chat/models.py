from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    message: str = Field(min_length=1, max_length=12_000)
    mode: Literal["LOW", "MEDIUM", "HARD"] = Field(default="LOW")
    auto_memory: bool = True


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    requested_mode: str
    executed_mode: str
    providers_used: list[str]
    status: Literal["success", "error", "partial"]
    error_message: Optional[str] = None
    extracted_memories: Optional[int] = None
    retrieved_memories: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
