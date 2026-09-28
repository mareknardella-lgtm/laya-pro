"""Fail-closed executor for registered trusted handlers.

No tool runs without a separately injected authorization provider. Inline execution
is only for trusted in-process Python handlers, not model-generated code.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from ..observability.executions import ExecutionHistoryError, ExecutionHistoryStatus, ExecutionHistoryStore
from .fingerprint import input_fingerprint
from .models import ExecutionMode, ToolAuthorization, ToolExecutionResult, ToolExecutionStatus
from .registry import ToolDefinition, ToolRegistry, ToolRegistryError

logger = logging.getLogger("laya.tools.executor")
AuthorizationProvider = Callable[[ToolDefinition, dict, object, str, Optional[str]], Awaitable[ToolAuthorization]]
ActionReferenceProvider = Callable[[ToolDefinition, dict, object], Optional[str]]


class ToolExecutionError(RuntimeError):
    """Tool could not safely be executed."""


class ToolAuthorizationError(ToolExecutionError):
    """Tool does not have a valid, matching authorization capability."""


class DuplicateExecutionError(ToolExecutionError):
    """An idempotency key was reused for a different operation."""


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        authorizer: Optional[AuthorizationProvider] = None,
        max_cached_executions: int = 1024,
        action_reference: Optional[ActionReferenceProvider] = None,
        history: Optional[ExecutionHistoryStore] = None,
    ) -> None:
        if max_cached_executions < 1:
            raise ValueError("max_cached_executions must be positive")
        self._registry = registry
        self._authorizer = authorizer
        self._action_reference = action_reference
        self._history = history
        self._max_cached_executions = max_cached_executions
        self._lock: Optional[asyncio.Lock] = None
        self._inflight: dict[str, tuple[str, asyncio.Task[ToolExecutionResult]]] = {}
        self._completed: dict[str, tuple[str, ToolExecutionResult]] = {}

    async def execute(
        self,
        tool_id: str,
        parameters: dict,
        context: object,
        idempotency_key: str,
        approval_id: Optional[str] = None,
    ) -> ToolExecutionResult:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ToolExecutionError("A bounded idempotency_key is required")
        try:
            definition = self._registry.get(tool_id)
        except ToolRegistryError as exc:
            raise ToolExecutionError("Tool is not registered") from exc
        try:
            validated_input = definition.input_model.model_validate(parameters)
        except ValidationError as exc:
            raise ToolExecutionError("Tool input did not match its registered schema") from exc
        normalized = validated_input.model_dump(mode="json")
        project = getattr(context, "project", None)
        workflow = getattr(context, "workflow", None)
        project_id = getattr(project, "project_id", None) or getattr(context, "project_id", None)
        workflow_id = getattr(workflow, "workflow_id", None) or getattr(context, "workflow_id", None)
        action_ref = self._action_reference(definition, normalized, context) if self._action_reference else None
        fingerprint = (
            input_fingerprint(tool_id, {"parameters": normalized, "action_ref": action_ref}, project_id, workflow_id)
            if action_ref is not None
            else input_fingerprint(tool_id, normalized, project_id, workflow_id)
        )
        if self._authorizer is None:
            raise ToolAuthorizationError("No policy authorization provider is configured")
        if definition.execution_mode != ExecutionMode.INLINE_TRUSTED:
            raise ToolExecutionError("Isolated process execution is unavailable; refusing unsafe fallback")

        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            completed = self._completed.get(idempotency_key)
            if completed:
                previous_fingerprint, previous_result = completed
                if previous_fingerprint != fingerprint:
                    raise DuplicateExecutionError("Idempotency key was used for a different action")
                return previous_result.model_copy(update={"replayed": True})
            current = self._inflight.get(idempotency_key)
            if current:
                previous_fingerprint, task = current
                if previous_fingerprint != fingerprint:
                    raise DuplicateExecutionError("Idempotency key is executing a different action")
            else:
                task = asyncio.create_task(
                    self._authorize_and_run(
                        definition,
                        validated_input,
                        normalized,
                        context,
                        fingerprint,
                        idempotency_key,
                        approval_id,
                        action_ref,
                    )
                )
                self._inflight[idempotency_key] = (fingerprint, task)
        return await asyncio.shield(task)

    @staticmethod
    def _validate_authorization(definition: ToolDefinition, authorization: ToolAuthorization, fingerprint: str) -> None:
        if authorization.tool_id != definition.tool_id or authorization.input_fingerprint != fingerprint:
            raise ToolAuthorizationError("Authorization does not match the exact tool and input")
        if time.time() >= authorization.expires_at_epoch:
            raise ToolAuthorizationError("Authorization has expired")
        if not definition.required_permissions.issubset(authorization.permissions):
            raise ToolAuthorizationError("Authorization lacks required tool permissions")

    async def _authorize_and_run(
        self,
        definition: ToolDefinition,
        validated_input: BaseModel,
        normalized: dict,
        context: object,
        fingerprint: str,
        idempotency_key: str,
        approval_id: Optional[str],
        action_ref: Optional[str],
    ) -> ToolExecutionResult:
        current_task = asyncio.current_task()
        try:
            authorization = await self._authorizer(definition, normalized, context, fingerprint, approval_id, action_ref)
            self._validate_authorization(definition, authorization, fingerprint)
            return await self._run_and_store(
                definition,
                validated_input,
                context,
                fingerprint,
                idempotency_key,
            )
        finally:
            if self._lock is not None:
                async with self._lock:
                    current = self._inflight.get(idempotency_key)
                    if current is not None and current[1] is current_task:
                        self._inflight.pop(idempotency_key, None)

    async def _run_and_store(
        self,
        definition: ToolDefinition,
        validated_input: BaseModel,
        context: object,
        fingerprint: str,
        idempotency_key: str,
    ) -> ToolExecutionResult:
        execution_id = str(uuid4())
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        workflow = getattr(context, "workflow", None)
        project = getattr(context, "project", None)
        project_id = getattr(project, "project_id", None) or getattr(context, "project_id", None)
        workflow_id = getattr(workflow, "workflow_id", None) or getattr(context, "workflow_id", None)
        step_id = getattr(workflow, "step_id", None)
        request_id = getattr(context, "request_id", None) or idempotency_key
        history_started = False
        if self._history is not None:
            try:
                self._history.start(
                    execution_id,
                    idempotency_key,
                    request_id,
                    project_id,
                    workflow_id,
                    step_id,
                    definition.tool_id,
                    started_at,
                )
                history_started = True
            except Exception:
                logger.exception("Execution history could not record execution start", extra={"execution_id": execution_id})
        try:
            output = await asyncio.wait_for(
                definition.handler(validated_input, context),
                timeout=definition.timeout_seconds,
            )
            if not isinstance(output, BaseModel):
                raise TypeError("Tool handler must return its registered Pydantic output model")
            validated_output = definition.output_model.model_validate(output.model_dump(mode="python"))
            result = ToolExecutionResult(
                execution_id=execution_id,
                idempotency_key=idempotency_key,
                tool_id=definition.tool_id,
                status=ToolExecutionStatus.SUCCEEDED,
                output=validated_output.model_dump(mode="json"),
            )
        except asyncio.TimeoutError:
            result = ToolExecutionResult(
                execution_id=execution_id,
                idempotency_key=idempotency_key,
                tool_id=definition.tool_id,
                status=ToolExecutionStatus.TIMED_OUT,
                error_code="tool_timeout",
                error_message="Tool exceeded its configured timeout",
            )
        except asyncio.CancelledError:
            if history_started and self._history is not None:
                try:
                    self._history.finish(
                        execution_id,
                        ExecutionHistoryStatus.CANCELLED,
                        datetime.now(timezone.utc),
                        int((time.monotonic() - started_monotonic) * 1000),
                        error_code="execution_cancelled",
                        error_summary="Execution was cancelled",
                    )
                except ExecutionHistoryError:
                    logger.exception("Cancelled execution history could not be completed", extra={"execution_id": execution_id})
            raise
        except Exception as exc:
            logger.warning("Registered tool failed", extra={"execution_id": execution_id, "status": "failed"})
            result = ToolExecutionResult(
                execution_id=execution_id,
                idempotency_key=idempotency_key,
                tool_id=definition.tool_id,
                status=ToolExecutionStatus.FAILED,
                error_code="tool_failed",
                error_message=f"Tool failed ({type(exc).__name__})",
            )
        if history_started and self._history is not None:
            history_status = {
                ToolExecutionStatus.SUCCEEDED: ExecutionHistoryStatus.SUCCEEDED,
                ToolExecutionStatus.FAILED: ExecutionHistoryStatus.FAILED,
                ToolExecutionStatus.TIMED_OUT: ExecutionHistoryStatus.TIMED_OUT,
                ToolExecutionStatus.CANCELLED: ExecutionHistoryStatus.CANCELLED,
            }[result.status]
            try:
                self._history.finish(
                    execution_id,
                    history_status,
                    datetime.now(timezone.utc),
                    int((time.monotonic() - started_monotonic) * 1000),
                    error_code=result.error_code,
                    error_summary=result.error_message,
                )
            except ExecutionHistoryError:
                logger.exception("Execution history lifecycle could not be completed", extra={"execution_id": execution_id})
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if len(self._completed) >= self._max_cached_executions:
                self._completed.pop(next(iter(self._completed)))
            self._completed[idempotency_key] = (fingerprint, result)
        return result

    async def cancel(self, idempotency_key: str) -> bool:
        if self._lock is None:
            return False
        async with self._lock:
            current = self._inflight.get(idempotency_key)
            if current is None:
                return False
            _fingerprint, task = current
            if not task.done():
                task.cancel()
                return True
            return False
