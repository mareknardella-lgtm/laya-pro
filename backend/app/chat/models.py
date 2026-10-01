"""Public chat contracts: tiers, requests, responses and the pipeline trace."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Tier(str, Enum):
    """How much of the hybrid pipeline a request is allowed to spend."""

    LOW = "LOW"
    """Fast tier only: one Nemotron call, no reasoning pass."""

    MEDIUM = "MEDIUM"
    """laya-coreml plans, Nemotron writes the answer."""

    HARD = "HARD"
    """laya-coreml plans deeply, Nemotron writes, laya-coreml critiques and the
    answer is revised until approved or the refinement budget runs out."""


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=16_000)
    session_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    tier: Tier = Tier.MEDIUM
    auto_memory: bool = True

    @field_validator("message")
    @classmethod
    def message_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be blank")
        return value


class TraceStep(BaseModel):
    """One hop through the pipeline, recorded so the merge is auditable."""

    model_config = ConfigDict(extra="forbid")

    stage: str = Field(description="e.g. 'nemotron.generate', 'laya.reason'")
    engine: str
    latency_ms: int = 0
    detail: Optional[str] = None


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    session_id: str
    tier: Tier
    status: str = "success"
    engines_used: list[str] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)
    confidence: Optional[float] = None
    refinements: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)