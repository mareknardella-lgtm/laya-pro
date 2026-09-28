"""Validated context passed between decisions in a workflow cycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..systems.laya.models import DecisionRequest


@dataclass(frozen=True)
class WorkflowContext:
    workflow_id: Optional[str]
    project_id: Optional[str]
    iteration: int
    data: dict[str, Any] = field(default_factory=dict)
    step_id: Optional[str] = None

    @classmethod
    def from_request(cls, request: DecisionRequest) -> "WorkflowContext":
        return cls(
            workflow_id=request.workflow_id,
            project_id=request.project_id,
            iteration=request.iteration,
            data=dict(request.context),
        )
