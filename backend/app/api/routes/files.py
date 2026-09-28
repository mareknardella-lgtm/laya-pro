"""Project file APIs share the registered tool, policy, approval, and executor boundary."""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ...changes.rollback import FileOperationError
from ...context.project_context import ProjectContextManager
from ...context.workflow_context import WorkflowContext
from ...core.execution_context import ExecutionContext
from ...sandbox.manager import SandboxViolation
from ...security.approval_gate import ApprovalError, ApprovalGate
from ...tools.executor import ToolAuthorizationError, ToolExecutionError
from ...tools.models import ToolExecutionResult
from ...security.session import authorize_operator as _auth_op

router = APIRouter(prefix="/api/v1/files", tags=["files"])


class ReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=1_024)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    project_id: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=1_024)
    content: str = Field(max_length=16_777_216)
    expected_sha256: Optional[str] = Field(default=None, min_length=64, max_length=64)
    approval_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: str = Field(min_length=1, max_length=64)
    operation_id: str = Field(min_length=1, max_length=128)
    approval_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)


def _operator(request: Request, token: Optional[str]) -> None:
    _auth_op(request, token)


def _context(request: Request, project_id: str) -> ExecutionContext:
    projects: ProjectContextManager = request.app.state.project_contexts
    project = projects.resolve(project_id)
    workflow = WorkflowContext(workflow_id=None, project_id=project_id, iteration=0, data={})
    return ExecutionContext(request_id="file-operation", project=project, workflow=workflow)


async def _execute(request: Request, tool_id: str, parameters: dict, context: ExecutionContext, key: str, approval_id: Optional[str]) -> ToolExecutionResult:
    try:
        return await request.app.state.tool_executor.execute(tool_id, parameters, context, key, approval_id)
    except (ToolAuthorizationError, PermissionError) as exc:
        raise HTTPException(status_code=403, detail={"code": "policy_denied", "message": str(exc)}) from exc
    except ToolExecutionError as exc:
        raise HTTPException(status_code=422, detail={"code": "tool_execution_rejected", "message": str(exc)}) from exc
    except (SandboxViolation, FileNotFoundError, FileOperationError) as exc:
        raise HTTPException(status_code=400, detail={"code": "file_operation_rejected", "message": str(exc)}) from exc


def _approved_direct_action(request: Request, approval_id: str, tool_id: str, parameters: dict, project_id: str) -> None:
    try:
        approval = request.app.state.approval_gate.get(approval_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=404, detail={"code": "approval_not_found", "message": str(exc)}) from exc
    definition = request.app.state.tool_registry.get(tool_id)
    definition.input_model.model_validate(parameters)
    action_ref = parameters.get("operation_id") if tool_id == "file.rollback" else None
    expected = ApprovalGate.fingerprint(
        definition,
        parameters,
        project_id,
        None,
        action_ref,
    )
    if (approval.status.value != "approved"
            or approval.workflow_id is not None
            or approval.project_id != project_id
            or approval.tool_id != tool_id
            or approval.input_fingerprint != expected):
        raise HTTPException(status_code=403, detail={"code": "approval_scope_mismatch", "message": "Approval is not approved for this exact direct file action."})


@router.post("/read", response_model=ToolExecutionResult)
async def read_file(
    body: ReadRequest,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> ToolExecutionResult:
    _operator(request, x_laya_approval_token)
    context = _context(request, body.project_id)
    return await _execute(request, "file.read", {"path": body.path}, context, body.idempotency_key, None)


@router.post("/replace", response_model=ToolExecutionResult)
async def replace_file(
    body: ReplaceRequest,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> ToolExecutionResult:
    _operator(request, x_laya_approval_token)
    context = _context(request, body.project_id)
    parameters = {"path": body.path, "content": body.content, "expected_sha256": body.expected_sha256}
    _approved_direct_action(request, body.approval_id, "file.replace", parameters, body.project_id)
    return await _execute(request, "file.replace", parameters, context, body.idempotency_key, body.approval_id)


@router.post("/rollback", response_model=ToolExecutionResult)
async def rollback_file(
    body: RollbackRequest,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> ToolExecutionResult:
    _operator(request, x_laya_approval_token)
    context = _context(request, body.project_id)
    try:
        record = request.app.state.rollback_manager.get_record(body.operation_id)
    except FileOperationError as exc:
        raise HTTPException(status_code=404, detail={"code": "change_not_found", "message": str(exc)}) from exc
    if record.project_id != body.project_id:
        raise HTTPException(status_code=403, detail={"code": "project_scope_mismatch", "message": "Change belongs to a different project."})
    parameters = {"operation_id": body.operation_id}
    _approved_direct_action(request, body.approval_id, "file.rollback", parameters, body.project_id)
    return await _execute(request, "file.rollback", parameters, context, body.idempotency_key, body.approval_id)
