"""Single-step orchestrator. Tool execution is deliberately delegated to later phases."""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from ..memory.manager import MemoryManager
from ..memory.models import MemoryScope
from ..security.approval_gate import ApprovalGate
from ..security.policy_engine import ApprovalRequiredError, PolicyDeniedError, PolicyEngine
from ..systems.laya.models import DecisionKind, DecisionRequest
from ..tools.executor import ToolAuthorizationError, ToolExecutionError, ToolExecutor
from ..tools.registry import ToolRegistry, ToolRegistryError
from ..workflows.engine import WorkflowEngine
from .action_router import ActionRouter
from .execution_context import ExecutionContext

logger = logging.getLogger("laya.orchestrator")


class OrchestrationStatus(str, Enum):
    ACTION_PROPOSED = "action_proposed"
    ACTION_EXECUTED = "action_executed"
    APPROVAL_REQUIRED = "approval_required"
    POLICY_DENIED = "policy_denied"
    EXECUTION_FAILED = "execution_failed"
    CLARIFICATION_REQUIRED = "clarification_required"
    REJECTED = "rejected"
    NO_ACTION = "no_action"
    DONE = "done"
    CONTINUE_PENDING = "continue_pending"


class DecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    request_id: str
    workflow_id: Optional[str]
    project_id: Optional[str]
    iteration: int
    status: OrchestrationStatus
    execution_status: str
    kind: DecisionKind
    reason: str
    action_id: Optional[str] = None
    parameters: dict = {}
    relevant_context: dict = {}
    missing_information: list[str] = []
    execution_result: Optional[dict] = None
    approval_id: Optional[str] = None
    error_message: Optional[str] = None


class Orchestrator:
    def __init__(
        self,
        router: ActionRouter,
        policy_engine: Optional[PolicyEngine] = None,
        approval_gate: Optional[ApprovalGate] = None,
        tool_registry: Optional[ToolRegistry] = None,
        tool_executor: Optional[ToolExecutor] = None,
        memory_manager: Optional[MemoryManager] = None,
        workflow_engine: Optional[WorkflowEngine] = None,
    ) -> None:
        self._router = router
        self._policy_engine = policy_engine
        self._approval_gate = approval_gate
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._memory_manager = memory_manager
        self._workflow_engine = workflow_engine

    async def process(self, request: DecisionRequest) -> DecisionResult:
        context, envelope = await self._router.route(request)
        decision = envelope.decision
        status = {
            DecisionKind.ACTION: OrchestrationStatus.ACTION_PROPOSED,
            DecisionKind.CONTINUE: OrchestrationStatus.CONTINUE_PENDING,
            DecisionKind.CLARIFICATION_REQUIRED: OrchestrationStatus.CLARIFICATION_REQUIRED,
            DecisionKind.REJECT: OrchestrationStatus.REJECTED,
            DecisionKind.NO_ACTION: OrchestrationStatus.NO_ACTION,
            DecisionKind.DONE: OrchestrationStatus.DONE,
        }[decision.kind]
        execution_status = "not_executed"
        if decision.kind in {DecisionKind.ACTION, DecisionKind.CONTINUE}:
            execution_status = "awaiting_registry_and_policy"

        execution_result: Optional[dict] = None
        approval_id: Optional[str] = request.approval_id
        error_message: Optional[str] = None

        # When execution is explicitly requested for actionable decisions and execution components are wired:
        if request.execute and decision.kind in {DecisionKind.ACTION, DecisionKind.CONTINUE} and decision.action_id:
            if not self._policy_engine or not self._tool_registry or not self._tool_executor:
                execution_status = "execution_unavailable"
                status = OrchestrationStatus.EXECUTION_FAILED
                error_message = "Execution pipeline components are not wired to the orchestrator"
            else:
                try:
                    tool = self._tool_registry.get(decision.action_id)
                    evaluation = self._policy_engine.evaluate(tool, decision.parameters, context)
                    if not evaluation.allowed:
                        status = OrchestrationStatus.POLICY_DENIED
                        execution_status = "policy_denied"
                        error_message = evaluation.reason
                    elif evaluation.requires_approval and not approval_id:
                        status = OrchestrationStatus.APPROVAL_REQUIRED
                        execution_status = "approval_required"
                        if self._approval_gate:
                            action_ref = decision.parameters.get("operation_id") if tool.tool_id == "file.rollback" else None
                            app_req = self._approval_gate.request(
                                definition=tool,
                                parameters=decision.parameters,
                                requested_by="system:orchestrator",
                                reason=f"Approval required for {decision.action_id}: {decision.reason}",
                                project_id=context.workflow.project_id,
                                workflow_id=context.workflow.workflow_id,
                                request_id=context.request_id,
                                action_ref=action_ref,
                            )
                            approval_id = app_req.approval_id
                    else:
                        idempotency_key = f"{context.request_id}:{decision.action_id}"
                        res = await self._tool_executor.execute(
                            tool_id=decision.action_id,
                            parameters=decision.parameters,
                            context=context,
                            idempotency_key=idempotency_key,
                            approval_id=approval_id,
                        )
                        execution_result = res.model_dump(mode="json")
                        execution_status = res.status.value
                        if res.status.value == "succeeded":
                            status = OrchestrationStatus.ACTION_EXECUTED
                            # Update memory if project context exists
                            if self._memory_manager and context.project:
                                try:
                                    self._memory_manager.put(
                                        project_id=context.project.project_id,
                                        key=f"last_action_{decision.action_id}",
                                        value={"status": "succeeded", "request_id": context.request_id},
                                        scope=MemoryScope.PERSISTENT,
                                    )
                                except Exception:
                                    logger.warning("Could not record action execution in memory", exc_info=True)
                        else:
                            status = OrchestrationStatus.EXECUTION_FAILED
                            error_message = res.error_message or res.error_code
                except ToolRegistryError as exc:
                    status = OrchestrationStatus.EXECUTION_FAILED
                    execution_status = "tool_not_found"
                    error_message = str(exc)
                except PolicyDeniedError as exc:
                    status = OrchestrationStatus.POLICY_DENIED
                    execution_status = "policy_denied"
                    error_message = str(exc)
                except ApprovalRequiredError as exc:
                    status = OrchestrationStatus.APPROVAL_REQUIRED
                    execution_status = "approval_required"
                    error_message = str(exc)
                except ToolAuthorizationError as exc:
                    status = OrchestrationStatus.POLICY_DENIED
                    execution_status = "authorization_failed"
                    error_message = str(exc)
                except ToolExecutionError as exc:
                    status = OrchestrationStatus.EXECUTION_FAILED
                    execution_status = "execution_failed"
                    error_message = str(exc)
                except Exception as exc:
                    status = OrchestrationStatus.EXECUTION_FAILED
                    execution_status = "error"
                    error_message = f"{type(exc).__name__}: {str(exc)}"

        result = DecisionResult(
            decision_id=str(uuid4()),
            request_id=context.request_id,
            workflow_id=context.workflow.workflow_id,
            project_id=context.workflow.project_id,
            iteration=context.workflow.iteration,
            status=status,
            execution_status=execution_status,
            kind=decision.kind,
            reason=decision.reason,
            action_id=decision.action_id,
            parameters=decision.parameters,
            relevant_context=decision.relevant_context,
            missing_information=decision.missing_information,
            execution_result=execution_result,
            approval_id=approval_id,
            error_message=error_message,
        )
        logger.info(
            "Decision processed",
            extra={
                "request_id": result.request_id,
                "workflow_id": result.workflow_id,
                "decision_id": result.decision_id,
                "status": result.status.value,
            },
        )
        return result
