"""Typed metadata and results for the trusted tool boundary."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Type
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExecutionMode(str, Enum):
    INLINE_TRUSTED = "inline_trusted"
    ISOLATED_PROCESS = "isolated_process"


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    risk_level: RiskLevel
    required_permissions: frozenset[str]
    timeout_seconds: float
    execution_mode: ExecutionMode
    prerequisites: tuple[str, ...]
    cancellable: bool


class ToolAuthorization(BaseModel):
    """Internal capability object returned only by an injected authorization provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str = Field(min_length=1, max_length=128)
    input_fingerprint: str = Field(min_length=64, max_length=64)
    permissions: frozenset[str] = frozenset()
    approval_id: Optional[str] = None
    expires_at_epoch: float
    authorization_id: str = Field(default_factory=lambda: str(uuid4()))


class ToolExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ToolExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    idempotency_key: str
    tool_id: str
    status: ToolExecutionStatus
    output: Optional[dict[str, Any]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    replayed: bool = False
