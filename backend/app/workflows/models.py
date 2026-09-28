"""Typed, bounded workflow plan and lifecycle contracts."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkflowStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=64)
    tool_id: str = Field(min_length=1, max_length=128)
    parameters: dict[str, Any]
    depends_on: list[str] = Field(default_factory=list, max_length=32)


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=32)
    timeout_seconds: float = Field(default=300.0, gt=0, le=3_600)

    @model_validator(mode="after")
    def validate_dependencies(self) -> "WorkflowDefinition":
        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Workflow step IDs must be unique")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", step_id) for step_id in ids):
            raise ValueError("Workflow step IDs may contain letters, digits, hyphens, and underscores")
        known = set(ids)
        for step in self.steps:
            if step.step_id in step.depends_on:
                raise ValueError("Workflow step cannot depend on itself")
            if any(dependency not in known for dependency in step.depends_on):
                raise ValueError("Workflow step dependency does not exist")
        resolved: set[str] = set()
        pending = {step.step_id: set(step.depends_on) for step in self.steps}
        while pending:
            ready = {step_id for step_id, deps in pending.items() if deps.issubset(resolved)}
            if not ready:
                raise ValueError("Workflow dependencies contain a cycle")
            resolved.update(ready)
            for step_id in ready:
                pending.pop(step_id)
        return self


class WorkflowRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    project_id: Optional[str]
    status: WorkflowStatus
    definition: WorkflowDefinition
    state: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    version: int
