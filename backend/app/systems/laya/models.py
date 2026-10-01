"""Typed contract for laya-coreml deep reasoning.

This is the orchestrator's own contract with the runtime. It carries provenance
so a response can never imply a reasoning pass that did not happen.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReasoningDepth(str, Enum):
    STANDARD = "standard"
    """MEDIUM tier: decompose into a short plan."""

    DEEP = "deep"
    """HARD tier: cross-check constraints and produce a multi-step plan."""


class ReasoningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    problem: str = Field(min_length=1, max_length=32_000)
    depth: ReasoningDepth = ReasoningDepth.STANDARD
    intent: Optional[str] = Field(default=None, max_length=500)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def bounded_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(str(value)) > 32_000:
            raise ValueError("Reasoning context is too large")
        return value


class ReasoningStep(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    goal: str = Field(min_length=1, max_length=1_000)
    rationale: str = Field(min_length=1, max_length=4_000)
    expected_output: str = Field(min_length=1, max_length=2_000)


class ReasoningPlan(BaseModel):
    """A validated, executable plan produced by the deep reasoning runtime."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=128)
    steps: list[ReasoningStep] = Field(min_length=1, max_length=32)
    conclusion: str = Field(min_length=1, max_length=8_000)
    confidence: float = Field(ge=0.0, le=1.0)
    depth: ReasoningDepth = ReasoningDepth.STANDARD
    assumptions: list[str] = Field(default_factory=list, max_length=32)
    open_questions: list[str] = Field(default_factory=list, max_length=32)
    engine: str = Field(default="laya-coreml", min_length=1, max_length=128)
    runtime_configured: bool = True

    @model_validator(mode="after")
    def enforce_depth_contract(self) -> "ReasoningPlan":
        if self.depth is ReasoningDepth.DEEP and len(self.steps) < 2:
            raise ValueError("A deep plan requires at least two steps")
        if not self.runtime_configured:
            raise ValueError("A response cannot claim that the configured runtime is unavailable")
        return self


class CritiqueVerdict(str, Enum):
    APPROVED = "approved"
    REVISIONS_REQUIRED = "revisions_required"
    REJECTED = "rejected"


class CritiqueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    draft: str = Field(min_length=1, max_length=65_536)
    plan: ReasoningPlan
    requirements: list[str] = Field(default_factory=list, max_length=32)


class Critique(BaseModel):
    """A structured judgement of a draft answer against its plan."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=128)
    verdict: CritiqueVerdict
    issues: list[str] = Field(default_factory=list, max_length=32)
    summary: str = Field(min_length=1, max_length=4_000)
    engine: str = Field(default="laya-coreml", min_length=1, max_length=128)

    @model_validator(mode="after")
    def enforce_verdict_contract(self) -> "Critique":
        if self.verdict is CritiqueVerdict.REVISIONS_REQUIRED and not self.issues:
            raise ValueError("REVISIONS_REQUIRED must list at least one issue")
        if self.verdict is CritiqueVerdict.APPROVED and self.issues:
            raise ValueError("APPROVED must not list issues")
        return self