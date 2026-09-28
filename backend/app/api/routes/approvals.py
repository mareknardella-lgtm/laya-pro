"""Human approval routes. Mutating approval decisions require a local operator token."""

from __future__ import annotations

from datetime import datetime, timezone
import hmac
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...changes.rollback import FileOperationError
from ...context.project_context import ProjectContextManager
from ...context.workflow_context import WorkflowContext
from ...core.execution_context import ExecutionContext
from ...security.approval_gate import ApprovalError, ApprovalGate, ApprovalRequest, ApprovalStatus
from ...security.session import authorize_operator as _auth_op
from ...security.policy_engine import PolicyEngine
from ...workflows.models import WorkflowStatus
from ...workflows.state import WorkflowStateError
from ...tools.registry import ToolRegistry, ToolRegistryError

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


class ApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_id: str = Field(min_length=1, max_length=128)
    parameters: dict
    reason: str = Field(min_length=1, max_length=1_000)
    project_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    workflow_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    action_ref: Optional[str] = Field(default=None, min_length=1, max_length=128)


def _authorize_operator(request: Request, token: Optional[str]) -> str:
    return _auth_op(request, token)


@router.post("", response_model=ApprovalRequest, status_code=201)
def request_approval(
    body: ApprovalCreateRequest,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> ApprovalRequest:
    _authorize_operator(request, x_laya_approval_token)
    registry: ToolRegistry = request.app.state.tool_registry
    gate: ApprovalGate = request.app.state.approval_gate
    policy: PolicyEngine = request.app.state.policy_engine
    projects: ProjectContextManager = request.app.state.project_contexts
    try:
        definition = registry.get(body.tool_id)
    except ToolRegistryError as exc:
        raise HTTPException(status_code=404, detail={"code": "tool_not_registered", "message": str(exc)}) from exc
    project = projects.resolve(body.project_id)
    if body.workflow_id is not None:
        if body.action_ref is not None:
            raise HTTPException(status_code=422, detail={"code": "workflow_action_reference_invalid", "message": "Resource-bound rollback approvals must be created by the workflow engine."})
        try:
            workflow_record = request.app.state.workflow_engine.get(body.workflow_id)
        except WorkflowStateError as exc:
            raise HTTPException(status_code=404, detail={"code": "workflow_not_found", "message": str(exc)}) from exc
        if workflow_record.project_id != body.project_id or workflow_record.status != WorkflowStatus.AWAITING_APPROVAL:
            raise HTTPException(status_code=409, detail={"code": "workflow_approval_conflict", "message": "Workflow is not awaiting approval in the requested project."})
        pending_step_id = workflow_record.state.get("pending_step")
        step = next((item for item in workflow_record.definition.steps if item.step_id == pending_step_id), None)
        if step is None or step.tool_id != body.tool_id:
            raise HTTPException(status_code=409, detail={"code": "workflow_approval_mismatch", "message": "Approval must match the exact pending workflow step."})
        try:
            pending_parameters = definition.input_model.model_validate(step.parameters).model_dump(mode="json")
            requested_parameters = definition.input_model.model_validate(body.parameters).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail={"code": "tool_input_invalid", "message": "Approval parameters do not match the registered tool schema."}) from exc
        if pending_parameters != requested_parameters:
            raise HTTPException(status_code=409, detail={"code": "workflow_approval_mismatch", "message": "Approval must match the exact pending workflow step."})
        existing_approval_id = workflow_record.state.get("approval_requests", {}).get(pending_step_id)
        if existing_approval_id:
            try:
                existing_approval = gate.get(existing_approval_id)
            except ApprovalError:
                existing_approval = None
            action_ref = step.parameters.get("operation_id") if step.tool_id == "file.rollback" else None
            expected_fingerprint = ApprovalGate.fingerprint(
                definition,
                pending_parameters,
                workflow_record.project_id,
                workflow_record.workflow_id,
                action_ref,
            )
            if (
                existing_approval is not None
                and existing_approval.status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}
                and existing_approval.expires_at > datetime.now(timezone.utc)
                and existing_approval.project_id == workflow_record.project_id
                and existing_approval.workflow_id == workflow_record.workflow_id
                and existing_approval.tool_id == step.tool_id
                and existing_approval.input_fingerprint == expected_fingerprint
            ):
                return existing_approval
        raise HTTPException(status_code=409, detail={"code": "workflow_approval_missing", "message": "The pending workflow step has no matching approval request."})
    workflow = WorkflowContext(
        workflow_id=None,
        project_id=body.project_id,
        iteration=0,
        data={},
    )
    context = ExecutionContext(
        request_id=body.request_id or "approval-request",
        project=project,
        workflow=workflow,
    )
    try:
        evaluation = policy.evaluate(definition, body.parameters, context)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "tool_input_invalid", "message": "Approval parameters do not match the registered tool schema."}) from exc
    if not evaluation.allowed:
        raise HTTPException(status_code=403, detail={"code": "policy_denied", "message": evaluation.reason})
    if not evaluation.requires_approval:
        raise HTTPException(status_code=409, detail={"code": "approval_not_required", "message": "This low-risk tool does not require an approval."})
    if body.tool_id == "file.rollback" and body.action_ref is None:
        raise HTTPException(status_code=422, detail={"code": "action_reference_required", "message": "Rollback approval must be bound to its exact operation ID."})
    if body.action_ref is not None:
        if body.tool_id != "file.rollback" or body.action_ref != body.parameters.get("operation_id"):
            raise HTTPException(status_code=422, detail={"code": "action_reference_invalid", "message": "Resource-bound approvals are supported only for the exact registered rollback operation."})
        try:
            record = request.app.state.rollback_manager.get_record(body.action_ref)
        except FileOperationError as exc:
            raise HTTPException(status_code=404, detail={"code": "change_not_found", "message": "Rollback target does not exist."}) from exc
        if record.project_id != body.project_id:
            raise HTTPException(status_code=403, detail={"code": "project_scope_mismatch", "message": "Rollback target belongs to a different project."})
    return gate.request(
        definition=definition,
        parameters=body.parameters,
        requested_by="system:orchestrator",
        reason=body.reason,
        project_id=body.project_id,
        workflow_id=body.workflow_id,
        request_id=body.request_id,
        action_ref=body.action_ref,
    )


@router.get("", response_model=list[ApprovalRequest])
def list_approvals(
    request: Request,
    limit: int = 100,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> list[ApprovalRequest]:
    _authorize_operator(request, x_laya_approval_token)
    gate: ApprovalGate = request.app.state.approval_gate
    return gate.list_pending(limit)


@router.post("/{approval_id}/approve", response_model=ApprovalRequest)
def approve(
    approval_id: str,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> ApprovalRequest:
    actor_id = _authorize_operator(request, x_laya_approval_token)
    gate: ApprovalGate = request.app.state.approval_gate
    try:
        return gate.approve(approval_id, actor_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail={"code": "approval_conflict", "message": str(exc)}) from exc


@router.post("/{approval_id}/reject", response_model=ApprovalRequest)
def reject(
    approval_id: str,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> ApprovalRequest:
    actor_id = _authorize_operator(request, x_laya_approval_token)
    gate: ApprovalGate = request.app.state.approval_gate
    try:
        return gate.reject(approval_id, actor_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail={"code": "approval_conflict", "message": str(exc)}) from exc
