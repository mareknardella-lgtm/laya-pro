"""Immutable execution context assembled from validated request metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..context.project_context import ProjectContext
from ..context.workflow_context import WorkflowContext


@dataclass(frozen=True)
class ExecutionContext:
    request_id: str
    project: Optional[ProjectContext]
    workflow: WorkflowContext
