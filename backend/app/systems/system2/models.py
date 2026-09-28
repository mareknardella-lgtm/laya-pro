"""Normalized System 2 request and plain-text-only response contracts."""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=12_000)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def bounded_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(str(value)) > 16_000:
            raise ValueError("Generation context is too large")
        return value


class GenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    request_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=65_536)
    engine: str = Field(min_length=1, max_length=128)
