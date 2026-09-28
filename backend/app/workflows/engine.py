"""Persistent multi-step workflow engine using the exact tool and policy boundary."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import ValidationError

from ..config import Settings
from ..context.project_context import ProjectContextManager
from ..context.workflow_context import WorkflowContext
from ..core.execution_context import ExecutionContext
from ..security.approval_gate import ApprovalGate
from ..security.policy_engine import PolicyEngine
from ..tools.executor import ToolExecutor
from ..tools.models import ExecutionMode, ToolExecutionStatus
from ..tools.registry import ToolRegistry, ToolRegistryError
from .models import WorkflowDefinition, WorkflowRecord, WorkflowStatus
from .state import WorkflowStateStore

_SECRET_TEXT = re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/=-]{12,}|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b)", re.IGNORECASE)
_SECRET_KEY = re.compile(r"(password|secret|token|credential|api[_-]?key|private[_-]?key|authorization)", re.IGNORECASE)


class WorkflowError(ValueError):
    """Workflow cannot safely be created or transitioned."""


class WorkflowEngine:
    def __init__(
        self,
        settings: Settings,
        store: WorkflowStateStore,
        registry: ToolRegistry,
        projects: ProjectContextManager,
        policy: PolicyEngine,
        approvals: ApprovalGate,
        executor: ToolExecutor,
    ) -> None:
        self._settings = settings
        self._store = store
        self._registry = registry
        self._projects = projects
        self._policy = policy
        self._approvals = approvals
        self._executor = executor
        self._locks: dict[str, asyncio.Lock] = {}
        self._pause_requests: set[str] = set()
        self._cancel_requests: set[str] = set()

    def create(self, definition: WorkflowDefinition) -> WorkflowRecord:
        if len(definition.steps) > self._settings.max_workflow_steps:
            raise WorkflowError("Workflow exceeds the configured step limit")
        self._projects.resolve(definition.project_id)
        for step in definition.steps:
            try:
                tool = self._registry.get(step.tool_id)
            except ToolRegistryError as exc:
                raise WorkflowError("Workflow references a tool that is not registered") from exc
            if tool.execution_mode != ExecutionMode.INLINE_TRUSTED:
                raise WorkflowError("Workflow references an unavailable isolated execution mode")
            try:
                tool.input_model.model_validate(step.parameters)
            except ValidationError as exc:
                raise WorkflowError(f"Workflow parameters are invalid for step {step.step_id}") from exc
            self._reject_sensitive(step.parameters)
            if len(json.dumps(step.parameters, ensure_ascii=False)) > 16_384:
                raise WorkflowError("Workflow step parameters exceed the storage bound")
        return self._store.create(str(uuid4()), definition)

    def get(self, workflow_id: str) -> WorkflowRecord:
        return self._store.get(workflow_id)

    def list(self, limit: int = 100, project_ids: Optional[list[str]] = None) -> list[WorkflowRecord]:
        return self._store.list(limit, project_ids=project_ids)

    async def run(self, workflow_id: str) -> WorkflowRecord:
        lock = self._locks.setdefault(workflow_id, asyncio.Lock())
        async with lock:
            record = self._store.get(workflow_id)
            if record.status not in {WorkflowStatus.CREATED, WorkflowStatus.PAUSED}:
                raise WorkflowError(f"Workflow cannot run from status {record.status.value}; use resume for a pending approval")
            if record.status == WorkflowStatus.PAUSED and record.state.get("pending_step"):
                raise WorkflowError("Workflow has a pending approval; use resume to continue its approval lifecycle")
            self._pause_requests.discard(workflow_id)
            self._cancel_requests.discard(workflow_id)
            state = dict(record.state)
            if "deadline_epoch" not in state:
                state["deadline_epoch"] = time.time() + min(
                    record.definition.timeout_seconds,
                    self._settings.workflow_timeout_seconds,
                )
            running = self._store.checkpoint(workflow_id, state, WorkflowStatus.RUNNING, record.version, "workflow.started")
            return await self._run_steps_locked(running)

    async def resume(self, workflow_id: str, approval_ids: Optional[dict[str, str]] = None) -> WorkflowRecord:
        lock = self._locks.setdefault(workflow_id, asyncio.Lock())
        async with lock:
            record = self._store.get(workflow_id)
            if record.status not in {WorkflowStatus.CREATED, WorkflowStatus.PAUSED, WorkflowStatus.AWAITING_APPROVAL}:
                raise WorkflowError(f"Workflow cannot resume from status {record.status.value}")
            state = dict(record.state)
            known_steps = {step.step_id: step for step in record.definition.steps}
            pending_step = state.get("pending_step")
            provided = dict(approval_ids or {})
            linked_approval_id = state.get("approval_requests", {}).get(pending_step) if pending_step else None
            if pending_step:
                if not linked_approval_id:
                    raise WorkflowError("Current pending workflow step has no linked approval")
                supplied_approval_id = provided.get(pending_step)
                if supplied_approval_id is not None and supplied_approval_id != linked_approval_id:
                    raise WorkflowError("Resume approval must match the current step's recorded approval")
                provided[pending_step] = linked_approval_id
            elif provided:
                raise WorkflowError("Approval mappings are accepted only for the current pending workflow step")
            for step_id, approval_id in provided.items():
                if pending_step and step_id != pending_step:
                    raise WorkflowError("Only the current pending workflow step may be resumed with an approval")
                step = known_steps.get(step_id)
                if step is None or not approval_id or len(approval_id) > 128:
                    raise WorkflowError("Approval mapping must reference known workflow steps and valid approval IDs")
                approval = self._approvals.get(approval_id)
                if approval.expires_at <= datetime.now(timezone.utc):
                    raise WorkflowError("Workflow approval has expired")
                if approval.status.value != "approved":
                    raise WorkflowError("Current workflow approval must be approved before resume")
                action_ref = step.parameters.get("operation_id") if step.tool_id == "file.rollback" else None
                expected_fingerprint = ApprovalGate.fingerprint(
                    self._registry.get(step.tool_id),
                    step.parameters,
                    record.project_id,
                    workflow_id,
                    action_ref,
                )
                if (
                    approval.workflow_id != workflow_id
                    or approval.project_id != record.project_id
                    or approval.status.value != "approved"
                    or approval.tool_id != step.tool_id
                    or approval.input_fingerprint != expected_fingerprint
                ):
                    raise WorkflowError("Workflow approval must be approved and bound to this exact workflow action")
                state.setdefault("approval_ids", {})[step_id] = approval_id
            if "deadline_epoch" not in state:
                state["deadline_epoch"] = time.time() + min(
                    record.definition.timeout_seconds,
                    self._settings.workflow_timeout_seconds,
                )
            running = self._store.checkpoint(workflow_id, state, WorkflowStatus.RUNNING, record.version, "workflow.resumed")
            self._pause_requests.discard(workflow_id)
            self._cancel_requests.discard(workflow_id)
            return await self._run_steps_locked(running)

    def cleanup_locks(self) -> None:
        terminal_states = {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
        for workflow_id in list(self._locks.keys()):
            try:
                record = self._store.get(workflow_id)
                if record.status in terminal_states:
                    self._locks.pop(workflow_id, None)
            except Exception:
                pass

    async def _run_steps_locked(self, record: WorkflowRecord) -> WorkflowRecord:
        result = await self._run_steps_locked_inner(record)
        if result.status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}:
            self._locks.pop(record.workflow_id, None)
        return result

    async def _run_steps_locked_inner(self, record: WorkflowRecord) -> WorkflowRecord:
        workflow_id = record.workflow_id
        state = dict(record.state)
        deadline_epoch = float(state.get("deadline_epoch", time.time()))
        project = self._projects.resolve(record.project_id)
        completed = set(state.get("completed_steps", []))
        steps = record.definition.steps
        for step in steps:
            if step.step_id in completed:
                continue
            if time.time() >= deadline_epoch:
                state["error"] = "workflow_timeout"
                return self._store.checkpoint(workflow_id, state, WorkflowStatus.FAILED, record.version, "workflow.failed")
            if workflow_id in self._cancel_requests:
                self._cancel_requests.discard(workflow_id)
                return self._store.checkpoint(workflow_id, state, WorkflowStatus.CANCELLED, record.version, "workflow.cancelled")
            if workflow_id in self._pause_requests:
                self._pause_requests.discard(workflow_id)
                return self._store.checkpoint(workflow_id, state, WorkflowStatus.PAUSED, record.version, "workflow.paused")
            if not set(step.depends_on).issubset(completed):
                state["error"] = "unsatisfied_step_dependency"
                return self._store.checkpoint(workflow_id, state, WorkflowStatus.FAILED, record.version, "workflow.failed")
            tool = self._registry.get(step.tool_id)
            try:
                evaluation = self._policy.evaluate(tool, step.parameters, ExecutionContext(
                    request_id=workflow_id,
                    project=project,
                    workflow=WorkflowContext(
                        workflow_id=workflow_id,
                        project_id=record.project_id,
                        iteration=len(completed),
                        step_id=step.step_id,
                    ),
                ))
            except ValidationError as exc:
                state["error"] = f"policy_validation_failed:{type(exc).__name__}"
                return self._store.checkpoint(workflow_id, state, WorkflowStatus.FAILED, record.version, "workflow.failed")
            if not evaluation.allowed:
                state["error"] = "policy_denied"
                return self._store.checkpoint(workflow_id, state, WorkflowStatus.FAILED, record.version, "workflow.failed")
            context = ExecutionContext(
                request_id=workflow_id,
                project=project,
                workflow=WorkflowContext(
                    workflow_id=workflow_id,
                    project_id=record.project_id,
                    iteration=len(completed),
                    step_id=step.step_id,
                ),
            )
            approval_id = state.get("approval_ids", {}).get(step.step_id)
            if evaluation.requires_approval and approval_id is None:
                action_ref = step.parameters.get("operation_id") if tool.tool_id == "file.rollback" else None
                approval = self._approvals.request(
                    tool,
                    step.parameters,
                    requested_by="system:workflow-engine",
                    reason=f"Approval required for workflow step {step.step_id}",
                    project_id=record.project_id,
                    workflow_id=workflow_id,
                    request_id=workflow_id,
                    action_ref=action_ref,
                )
                state.setdefault("approval_requests", {})[step.step_id] = approval.approval_id
                state["pending_step"] = step.step_id
                return self._store.checkpoint(workflow_id, state, WorkflowStatus.AWAITING_APPROVAL, record.version, "workflow.awaiting_approval")
            try:
                result = await asyncio.wait_for(
                    self._executor.execute(
                        step.tool_id,
                        step.parameters,
                        context,
                        idempotency_key=f"{workflow_id}:{step.step_id}",
                        approval_id=approval_id,
                    ),
                    timeout=max(0.001, deadline_epoch - time.time()),
                )
            except asyncio.TimeoutError:
                state["error"] = "workflow_timeout"
                return self._store.checkpoint(workflow_id, state, WorkflowStatus.FAILED, record.version, "workflow.failed")
            except Exception as exc:
                state["error"] = f"tool_execution_rejected:{type(exc).__name__}"
                return self._store.checkpoint(workflow_id, state, WorkflowStatus.FAILED, record.version, "workflow.failed")
            result_metadata = {
                "execution_id": result.execution_id,
                "idempotency_key": result.idempotency_key,
                "tool_id": result.tool_id,
                "status": result.status.value,
                "output_present": result.output is not None,
                "output_size_bytes": len(json.dumps(result.output, ensure_ascii=False).encode("utf-8")) if result.output is not None else 0,
            }
            state.setdefault("step_results", {})[step.step_id] = result_metadata
            if result.status != ToolExecutionStatus.SUCCEEDED:
                state["error"] = result.error_code or result.status.value
                return self._store.checkpoint(workflow_id, state, WorkflowStatus.FAILED, record.version, "workflow.failed")
            completed.add(step.step_id)
            state["completed_steps"] = [item.step_id for item in steps if item.step_id in completed]
            state.pop("pending_step", None)
            state.get("approval_ids", {}).pop(step.step_id, None)
            record = self._store.checkpoint(workflow_id, state, WorkflowStatus.RUNNING, record.version, "workflow.step_completed")
        return self._store.checkpoint(workflow_id, state, WorkflowStatus.COMPLETED, record.version, "workflow.completed")

    async def pause(self, workflow_id: str) -> WorkflowRecord:
        lock = self._locks.setdefault(workflow_id, asyncio.Lock())
        if lock.locked():
            self._pause_requests.add(workflow_id)
            return self._store.get(workflow_id)
        async with lock:
            record = self._store.get(workflow_id)
            if record.status == WorkflowStatus.RUNNING:
                state = dict(record.state)
                return self._store.checkpoint(workflow_id, state, WorkflowStatus.PAUSED, record.version, "workflow.paused")
            if record.status not in {WorkflowStatus.CREATED, WorkflowStatus.AWAITING_APPROVAL}:
                raise WorkflowError(f"Workflow cannot pause from status {record.status.value}")
            state = dict(record.state)
            if record.status == WorkflowStatus.AWAITING_APPROVAL:
                state["resume_status"] = WorkflowStatus.AWAITING_APPROVAL.value
            return self._store.checkpoint(workflow_id, state, WorkflowStatus.PAUSED, record.version, "workflow.paused")

    async def cancel(self, workflow_id: str) -> WorkflowRecord:
        lock = self._locks.setdefault(workflow_id, asyncio.Lock())
        if lock.locked():
            self._cancel_requests.add(workflow_id)
            return self._store.get(workflow_id)
        async with lock:
            record = self._store.get(workflow_id)
            if record.status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}:
                raise WorkflowError(f"Workflow cannot cancel from status {record.status.value}")
            return self._store.checkpoint(workflow_id, record.state, WorkflowStatus.CANCELLED, record.version, "workflow.cancelled")

    @classmethod
    def _reject_sensitive(cls, value: Any, depth: int = 0) -> None:
        if depth > 8:
            raise WorkflowError("Workflow parameter nesting is too deep")
        if isinstance(value, dict):
            for key, child in value.items():
                if _SECRET_KEY.search(str(key)):
                    raise WorkflowError("Workflow parameters cannot persist secret or credential fields")
                cls._reject_sensitive(child, depth + 1)
        elif isinstance(value, (list, tuple)):
            for child in value:
                cls._reject_sensitive(child, depth + 1)
        elif isinstance(value, str) and _SECRET_TEXT.search(value):
            raise WorkflowError("Workflow parameters appear to contain a secret")
