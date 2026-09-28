"""Typed decision contract for normalized Laya System 1 output.

This is Laya Pro's adapter contract, not a claim about an external runtime's native schema.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DecisionKind(str, Enum):
    ACTION = "ACTION"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    REJECT = "REJECT"
    NO_ACTION = "NO_ACTION"
    DONE = "DONE"
    CONTINUE = "CONTINUE"


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    user_input: str = Field(min_length=1, max_length=12_000)
    project_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    workflow_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    context: dict[str, Any] = Field(default_factory=dict)
    iteration: int = Field(default=0, ge=0, le=64)
    execute: bool = False
    approval_id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @field_validator("context")
    @classmethod
    def context_size_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(str(value)) > 16_000:
            raise ValueError("Decision context is too large")
        return value


class LayaDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: DecisionKind
    reason: str = Field(min_length=1, max_length=2_000)
    action_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    parameters: dict[str, Any] = Field(default_factory=dict)
    relevant_context: dict[str, Any] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list, max_length=32)
    request_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def enforce_kind_contract(self) -> "LayaDecision":
        if self.kind in {DecisionKind.ACTION, DecisionKind.CONTINUE}:
            if not self.action_id:
                raise ValueError("ACTION and CONTINUE require an action_id")
            if not self.parameters:
                raise ValueError("ACTION and CONTINUE require non-empty, validatable parameters")
        elif self.action_id is not None or self.parameters:
            raise ValueError("Non-operational decisions must not contain an action_id or parameters")
        if self.kind == DecisionKind.CLARIFICATION_REQUIRED and not self.missing_information:
            raise ValueError("CLARIFICATION_REQUIRED must specify missing_information")
        if self.kind != DecisionKind.CLARIFICATION_REQUIRED and self.missing_information:
            raise ValueError("missing_information is valid only for CLARIFICATION_REQUIRED")
        return self


class DecisionEnvelope(BaseModel):
    """Normalized response with provenance; no runtime is implied by the schema."""

    model_config = ConfigDict(extra="forbid")

    decision: LayaDecision
    engine: str = "laya-system-1"
    runtime_configured: bool
