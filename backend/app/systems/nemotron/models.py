"""Typed contract for Nemotron 3.5 Lightning generation.

The schema describes what this project requires from the runtime; it is not a
claim about the runtime's native wire format.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RenderRole(str, Enum):
    """How the fast model is allowed to use the reasoning plan."""

    ANSWER = "answer"
    """Turn a reasoning plan into the final natural language response."""

    CRITIQUE = "critique"
    """Judge a draft against a plan and report gaps, in plain text."""

    DIRECT = "direct"
    """Answer the user with no plan at all (LOW tier)."""

    EXTRACT = "extract"
    """Pull durable facts out of a conversation for long-term memory."""


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=32_000)
    role: RenderRole = RenderRole.DIRECT
    context: dict[str, Any] = Field(default_factory=dict)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=32_768)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)

    @field_validator("context")
    @classmethod
    def bounded_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(str(value)) > 16_000:
            raise ValueError("Generation context is too large")
        return value


class GenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=65_536)
    engine: str = Field(min_length=1, max_length=128)