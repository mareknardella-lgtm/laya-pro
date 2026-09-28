"""Deterministic request routing; never infers tool authorization from language."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("laya.router")

from ..context.project_context import ProjectContextManager
from ..context.workflow_context import WorkflowContext
from ..memory.manager import MemoryManager, MemoryScope
from ..systems.laya.models import DecisionEnvelope, DecisionRequest
from .decision_engine import DecisionEngine
from .execution_context import ExecutionContext


class ActionRouter:
    def __init__(
        self,
        projects: ProjectContextManager,
        decision_engine: DecisionEngine,
        memory_manager: Optional[MemoryManager] = None,
    ) -> None:
        self._projects = projects
        self._decision_engine = decision_engine
        self._memory_manager = memory_manager

    async def route(self, request: DecisionRequest) -> tuple[ExecutionContext, DecisionEnvelope]:
        project = self._projects.resolve(request.project_id)
        workflow = WorkflowContext.from_request(request)
        execution_context = ExecutionContext(
            request_id=request.request_id,
            project=project,
            workflow=workflow,
        )

        # Context enrichment: If memory manager is available and project is resolved,
        # attach project-isolated memory context to the request if not already provided
        effective_request = request
        if self._memory_manager and project and not request.context.get("project_memory"):
            try:
                memories = self._memory_manager.list(project.project_id, scope=MemoryScope.PERSISTENT, limit=20)
                if memories:
                    mem_dict = {m.key: m.value for m in memories}
                    enriched_context = dict(request.context)
                    enriched_context["project_memory"] = mem_dict
                    effective_request = request.model_copy(update={"context": enriched_context})
            except Exception:
                logger.warning("Memory context enrichment failed for project %s", project.project_id, exc_info=True)

        decision = await self._decision_engine.decide(effective_request)
        return execution_context, decision
