"""Authenticated change history and approval-bound rollback API."""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ...context.workflow_context import WorkflowContext
from ...core.execution_context import ExecutionContext
from ...changes.rollback import FileOperationError
from ...security.approval_gate import ApprovalError
from ...tools.executor import ToolAuthorizationError, ToolExecutionError
from ...security.approval_gate import ApprovalGate
from ...tools.models import ToolExecutionResult
from ...security.session import authorize_operator as _auth_op

router = APIRouter(prefix="/api/v1/changes", tags=["changes"])


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: str = Field(min_length=1, max_length=64)
    operation_id: str = Field(min_length=1, max_length=128)
    approval_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)


def _operator(request: Request, token: Optional[str]) -> None:
    _auth_op(request, token)


@router.post("/{operation_id}/rollback", response_model=ToolExecutionResult)
async def rollback_change(
    operation_id: str,
    body: RollbackRequest,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> ToolExecutionResult:
    _operator(request, x_laya_approval_token)
    if operation_id != body.operation_id:
        raise HTTPException(status_code=422, detail={"code": "operation_id_mismatch", "message": "Path and body operation IDs must match."})
    try:
        record = request.app.state.rollback_manager.get_record(operation_id)
    except FileOperationError as exc:
        raise HTTPException(status_code=404, detail={"code": "change_not_found", "message": str(exc)}) from exc
    if record.project_id != body.project_id:
        raise HTTPException(status_code=403, detail={"code": "project_scope_mismatch", "message": "Change belongs to a different project."})
    try:
        approval = request.app.state.approval_gate.get(body.approval_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=404, detail={"code": "approval_not_found", "message": str(exc)}) from exc
    expected = ApprovalGate.fingerprint(
        request.app.state.tool_registry.get("file.rollback"),
        {"operation_id": operation_id},
        body.project_id,
        None,
        operation_id,
    )
    if (approval.status.value != "approved"
            or approval.workflow_id is not None
            or approval.project_id != body.project_id
            or approval.tool_id != "file.rollback"
            or approval.input_fingerprint != expected):
        raise HTTPException(status_code=403, detail={"code": "approval_scope_mismatch", "message": "Approval is not approved for this exact direct project rollback."})
    project = request.app.state.project_contexts.resolve(body.project_id)
    context = ExecutionContext(
        request_id="rollback-operation",
        project=project,
        workflow=WorkflowContext(workflow_id=None, project_id=body.project_id, iteration=0),
    )
    try:
        return await request.app.state.tool_executor.execute(
            "file.rollback",
            {"operation_id": operation_id},
            context,
            body.idempotency_key,
            body.approval_id,
        )
    except (ToolAuthorizationError, PermissionError) as exc:
        raise HTTPException(status_code=403, detail={"code": "policy_denied", "message": str(exc)}) from exc
    except ToolExecutionError as exc:
        raise HTTPException(status_code=422, detail={"code": "tool_execution_rejected", "message": str(exc)}) from exc


@router.get("/{operation_id}")
def get_change(
    operation_id: str,
    request: Request,
    project_id: str,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> dict:
    _operator(request, x_laya_approval_token)
    try:
        record = request.app.state.rollback_manager.get_record(operation_id)
    except FileOperationError as exc:
        raise HTTPException(status_code=404, detail={"code": "change_not_found", "message": str(exc)}) from exc
    if record.project_id != project_id:
        raise HTTPException(status_code=403, detail={"code": "project_scope_mismatch", "message": "Change belongs to a different project."})
    return record.__dict__
