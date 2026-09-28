"""Authenticated operator APIs for approvals, workflows, history, and diagnostics."""

from __future__ import annotations

import hmac
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ...context.project_context import ProjectContextError
from ...observability.activity import ActivityService, ApprovalDetail, DecisionPage, OperatorWorkflowSummary
from ...observability.executions import ExecutionDetail, ExecutionHistoryError, ExecutionHistoryStatus, ExecutionPage, RuntimeComponentStatus
from ...security.approval_gate import ApprovalError, ApprovalRequest
from ...security.session import authorize_operator as _auth_op, verify_local_origin, OperatorSession
from ...tools.registry import ToolRegistryError
from ...workflows.engine import WorkflowError
from ...workflows.models import WorkflowRecord
from ...workflows.state import WorkflowStateError

router = APIRouter(prefix="/api/v1/operator", tags=["operator"])


class WorkflowResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_ids: dict[str, str] = Field(default_factory=dict)


class SystemStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    api_status: str
    last_successful_health_check: Optional[datetime]
    latest_audit_event_at: Optional[datetime]
    last_successful_execution_at: Optional[datetime]
    audit_event_count: int
    decision_count: int
    pending_approval_count: int
    active_workflow_count: int
    execution_counts: dict[str, int]
    system1: RuntimeComponentStatus
    system2: RuntimeComponentStatus


def _authorize(request: Request, token: Optional[str]) -> None:
    _auth_op(request, token)


def _service(request: Request) -> ActivityService:
    return request.app.state.activity_service


def _selected_project_ids(request: Request, project_id: Optional[str]) -> Optional[list[str]]:
    """Return an explicit empty scope for unknown filters; never widen to all projects."""
    if project_id is None:
        return None
    return [project_id] if project_id in request.app.state.project_contexts.project_ids else []


def _check_selected_project(actual_project_id: Optional[str], selected_project_id: Optional[str]) -> None:
    if selected_project_id is not None and actual_project_id != selected_project_id:
        raise HTTPException(status_code=404, detail={"code": "project_record_not_found", "message": "Record is unavailable in the selected project."})


def _workflow_for_operator(request: Request, workflow_id: str, selected_project_id: Optional[str] = None) -> WorkflowRecord:
    try:
        record = request.app.state.workflow_engine.get(workflow_id)
    except WorkflowStateError as exc:
        raise HTTPException(status_code=404, detail={"code": "workflow_not_found", "message": "Workflow was not found."}) from exc
    try:
        request.app.state.project_contexts.resolve(record.project_id)
    except ProjectContextError as exc:
        raise HTTPException(status_code=403, detail={"code": "project_not_authorized", "message": "Workflow project is no longer authorized."}) from exc
    _check_selected_project(record.project_id, selected_project_id)
    return record


def _approval_workflow_step(request: Request, approval: ApprovalRequest):
    """Return the recorded linked step without exposing an orphaned or cross-project approval."""
    if approval.workflow_id is None:
        return None
    workflow = _workflow_for_operator(request, approval.workflow_id)
    if workflow.project_id != approval.project_id:
        raise HTTPException(status_code=404, detail={"code": "approval_unavailable", "message": "Approval is unavailable."})
    linked_steps = [
        step_id for step_id, linked_id in workflow.state.get("approval_requests", {}).items()
        if linked_id == approval.approval_id
    ]
    if len(linked_steps) != 1:
        return None
    step = next((item for item in workflow.definition.steps if item.step_id == linked_steps[0]), None)
    if step is None or step.tool_id != approval.tool_id:
        return None
    return step


def _authorize_approval_project(request: Request, approval: ApprovalRequest) -> None:
    if approval.project_id is None:
        raise HTTPException(status_code=403, detail={"code": "project_not_authorized", "message": "Approval project is no longer authorized."})
    try:
        request.app.state.project_contexts.resolve(approval.project_id)
    except ProjectContextError as exc:
        raise HTTPException(status_code=403, detail={"code": "project_not_authorized", "message": "Approval project is no longer authorized."}) from exc


def _authorize_approval_record(request: Request, approval: ApprovalRequest) -> None:
    """Expose lifecycle metadata only inside its authorized project, even if workflow linkage is stale."""
    _authorize_approval_project(request, approval)
    if approval.workflow_id is None:
        return
    try:
        workflow = request.app.state.workflow_engine.get(approval.workflow_id)
    except WorkflowStateError:
        return
    if workflow.project_id != approval.project_id:
        raise HTTPException(status_code=404, detail={"code": "approval_unavailable", "message": "Approval is unavailable."})


class SessionConnectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str
    status: str
    expires_at: datetime
    projects: list[str]


@router.post("/session/connect", response_model=SessionConnectResponse)
def connect_session(request: Request) -> SessionConnectResponse:
    verify_local_origin(request)
    session_mgr = getattr(request.app.state, "session_manager", None)
    if session_mgr is None:
        raise HTTPException(
            status_code=500,
            detail={"code": "session_manager_unavailable", "message": "Operator session manager is uninitialized."}
        )
    client_host = request.client.host if request.client else "127.0.0.1"
    session = session_mgr.create_session(client_host=client_host)
    projects = _service(request).projects()
    return SessionConnectResponse(
        session_token=session.session_token,
        status="connected",
        expires_at=session.expires_at,
        projects=projects,
    )


@router.post("/session/disconnect")
def disconnect_session(
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> dict[str, str]:
    if x_laya_approval_token:
        session_mgr = getattr(request.app.state, "session_manager", None)
        if session_mgr:
            session_mgr.revoke_session(x_laya_approval_token)
    return {"status": "disconnected"}


@router.get("/projects", response_model=list[str])
def project_list(
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> list[str]:
    _authorize(request, x_laya_approval_token)
    return _service(request).projects()


@router.get("/approvals", response_model=list[ApprovalDetail])
def approval_list(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> list[ApprovalDetail]:
    _authorize(request, x_laya_approval_token)
    details = _service(request).approvals(limit, project_ids=_selected_project_ids(request, project_id))
    visible: list[ApprovalDetail] = []
    for detail in details:
        approval = detail.approval
        try:
            _authorize_approval_record(request, approval)
        except HTTPException:
            continue
        visible.append(detail)
    return visible


@router.get("/approvals/{approval_id}", response_model=ApprovalDetail)
def approval_detail(
    approval_id: str,
    request: Request,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> ApprovalDetail:
    _authorize(request, x_laya_approval_token)
    try:
        approval = request.app.state.approval_gate.get(approval_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=404, detail={"code": "approval_not_found", "message": "Approval record is unavailable."}) from exc
    if approval.status.value == "pending" and approval.expires_at <= datetime.now(timezone.utc):
        request.app.state.approval_gate.list_pending(1)
        approval = request.app.state.approval_gate.get(approval_id)
    _authorize_approval_record(request, approval)
    _check_selected_project(approval.project_id, project_id)
    return _service(request).approval_detail(approval)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalRequest)
def approve(
    approval_id: str,
    request: Request,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> ApprovalRequest:
    _authorize(request, x_laya_approval_token)
    try:
        approval = request.app.state.approval_gate.get(approval_id)
        _authorize_approval_record(request, approval)
        _check_selected_project(approval.project_id, project_id)
        if approval.workflow_id is not None:
            _workflow_for_operator(request, approval.workflow_id)
            linked_step = _approval_workflow_step(request, approval)
            if linked_step is None:
                raise HTTPException(status_code=409, detail={"code": "approval_invalid", "message": "Associated workflow action is unavailable."})
            try:
                definition = request.app.state.tool_registry.get(linked_step.tool_id)
                action_ref = linked_step.parameters.get("operation_id") if linked_step.tool_id == "file.rollback" else None
                expected = request.app.state.approval_gate.fingerprint(definition, linked_step.parameters, approval.project_id, approval.workflow_id, action_ref)
                if expected != approval.input_fingerprint:
                    raise HTTPException(status_code=409, detail={"code": "approval_invalid", "message": "Approval fingerprint does not match the exact workflow action."})
            except (ToolRegistryError, ValueError, TypeError) as exc:
                raise HTTPException(status_code=409, detail={"code": "approval_invalid", "message": "The exact workflow approval fingerprint cannot be verified."}) from exc
        else:
            _authorize_approval_project(request, approval)
        if approval.status.value != "pending":
            raise HTTPException(status_code=409, detail={"code": "approval_conflict", "message": f"Approval is {approval.status.value}; only pending approvals may be decided."})
        if approval.expires_at <= datetime.now(timezone.utc):
            request.app.state.approval_gate.list_pending(1)
            raise HTTPException(status_code=409, detail={"code": "approval_expired", "message": "Approval expired before it could be approved."})
        details = _service(request).approval_detail(approval)
        if details.validation.status not in {"valid", "backend_recorded"}:
            raise HTTPException(status_code=409, detail={"code": "approval_invalid", "message": details.validation.message})
        return request.app.state.approval_gate.approve(approval_id, "local-operator")
    except ApprovalError as exc:
        code = "approval_expired" if "expired" in str(exc).lower() else "approval_conflict"
        raise HTTPException(status_code=409, detail={"code": code, "message": str(exc)}) from exc


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalRequest)
def reject(
    approval_id: str,
    request: Request,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> ApprovalRequest:
    _authorize(request, x_laya_approval_token)
    try:
        approval = request.app.state.approval_gate.get(approval_id)
        _authorize_approval_record(request, approval)
        _check_selected_project(approval.project_id, project_id)
        if approval.workflow_id is not None:
            _workflow_for_operator(request, approval.workflow_id)
            linked_step = _approval_workflow_step(request, approval)
            if linked_step is None:
                raise HTTPException(status_code=409, detail={"code": "approval_invalid", "message": "Associated workflow action is unavailable."})
            try:
                definition = request.app.state.tool_registry.get(linked_step.tool_id)
                action_ref = linked_step.parameters.get("operation_id") if linked_step.tool_id == "file.rollback" else None
                expected = request.app.state.approval_gate.fingerprint(definition, linked_step.parameters, approval.project_id, approval.workflow_id, action_ref)
                if expected != approval.input_fingerprint:
                    raise HTTPException(status_code=409, detail={"code": "approval_invalid", "message": "Approval fingerprint does not match the exact workflow action."})
            except (ToolRegistryError, ValueError, TypeError) as exc:
                raise HTTPException(status_code=409, detail={"code": "approval_invalid", "message": "The exact workflow approval fingerprint cannot be verified."}) from exc
        else:
            _authorize_approval_project(request, approval)
        if approval.status.value != "pending":
            raise HTTPException(status_code=409, detail={"code": "approval_conflict", "message": f"Approval is {approval.status.value}; only pending approvals may be decided."})
        if approval.expires_at <= datetime.now(timezone.utc):
            request.app.state.approval_gate.list_pending(1)
            raise HTTPException(status_code=409, detail={"code": "approval_expired", "message": "Approval expired before it could be rejected."})
        validation = _service(request).approval_detail(approval).validation
        if validation.status not in {"valid", "backend_recorded"}:
            raise HTTPException(status_code=409, detail={"code": "approval_invalid", "message": validation.message})
        return request.app.state.approval_gate.reject(approval_id, "local-operator")
    except ApprovalError as exc:
        code = "approval_expired" if "expired" in str(exc).lower() else "approval_conflict"
        raise HTTPException(status_code=409, detail={"code": code, "message": str(exc)}) from exc


@router.get("/workflows", response_model=list[OperatorWorkflowSummary])
def workflow_list(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> list[OperatorWorkflowSummary]:
    _authorize(request, x_laya_approval_token)
    visible: list[OperatorWorkflowSummary] = []
    for record in _service(request).workflows(limit, project_ids=_selected_project_ids(request, project_id)):
        try:
            request.app.state.project_contexts.resolve(record.project_id)
        except ProjectContextError:
            continue
        visible.append(_service(request).workflow_summary(record, include_events=False))
    return visible


@router.get("/workflows/{workflow_id}", response_model=OperatorWorkflowSummary)
def workflow_detail(
    workflow_id: str,
    request: Request,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> OperatorWorkflowSummary:
    _authorize(request, x_laya_approval_token)
    record = _workflow_for_operator(request, workflow_id, project_id)
    return _service(request).workflow_summary(record)


class CriticalAlertRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(min_length=1, max_length=128)
    workflow_id: Optional[str] = Field(default=None, max_length=128)
    execution_id: Optional[str] = Field(default=None, max_length=128)
    project_id: Optional[str] = Field(default=None, max_length=64)
    status: str = Field(default="interrupted", max_length=64)
    details: dict = Field(default_factory=dict)


@router.post("/critical-alert/event")
def record_critical_alert_event(
    body: CriticalAlertRecordRequest,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> dict[str, str]:
    _authorize(request, x_laya_approval_token)
    audit = request.app.state.audit_log
    from ...observability.audit import AuditEvent
    audit.record(AuditEvent(
        actor_id="operator:safety-monitor",
        event_type=f"critical_safety.{body.event_type}",
        project_id=body.project_id,
        workflow_id=body.workflow_id,
        execution_id=body.execution_id,
        status=body.status,
        details=body.details,
    ))
    return {"status": "recorded"}


@router.post("/workflows/{workflow_id}/pause", response_model=OperatorWorkflowSummary)
async def workflow_pause(
    workflow_id: str,
    request: Request,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> OperatorWorkflowSummary:
    _authorize(request, x_laya_approval_token)
    _workflow_for_operator(request, workflow_id, project_id)
    try:
        record = await request.app.state.workflow_engine.pause(workflow_id)
        return _service(request).workflow_summary(record)
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail={"code": "workflow_conflict", "message": str(exc)}) from exc


@router.post("/workflows/{workflow_id}/resume", response_model=OperatorWorkflowSummary)
async def workflow_resume(
    workflow_id: str,
    request: Request,
    body: Optional[WorkflowResumeRequest] = None,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> OperatorWorkflowSummary:
    _authorize(request, x_laya_approval_token)
    _workflow_for_operator(request, workflow_id, project_id)
    try:
        record = await request.app.state.workflow_engine.resume(workflow_id, body.approval_ids if body else {})
        return _service(request).workflow_summary(record)
    except WorkflowStateError as exc:
        raise HTTPException(status_code=409, detail={"code": "workflow_conflict", "message": str(exc)}) from exc
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail={"code": "workflow_conflict", "message": str(exc)}) from exc


@router.post("/workflows/{workflow_id}/cancel", response_model=OperatorWorkflowSummary)
async def workflow_cancel(
    workflow_id: str,
    request: Request,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> OperatorWorkflowSummary:
    _authorize(request, x_laya_approval_token)
    _workflow_for_operator(request, workflow_id, project_id)
    try:
        record = await request.app.state.workflow_engine.cancel(workflow_id)
        return _service(request).workflow_summary(record)
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail={"code": "workflow_conflict", "message": str(exc)}) from exc


@router.get("/executions", response_model=ExecutionPage)
def execution_list(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    q: Optional[str] = Query(default=None, max_length=128),
    status: Optional[ExecutionHistoryStatus] = None,
    workflow_id: Optional[str] = Query(default=None, max_length=128),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> ExecutionPage:
    _authorize(request, x_laya_approval_token)
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if since is not None and until is not None and since > until:
        raise HTTPException(status_code=422, detail={"code": "date_range_invalid", "message": "since must be earlier than or equal to until."})
    project_ids = _selected_project_ids(request, project_id)
    if workflow_id is not None:
        workflow_record = _workflow_for_operator(request, workflow_id, project_id)
        project_ids = [workflow_record.project_id] if workflow_record.project_id is not None else []
    return _service(request).executions(limit=limit, offset=offset, query=q, status=status, workflow_id=workflow_id, since=since, until=until, project_ids=project_ids)


@router.get("/executions/{execution_id}", response_model=ExecutionDetail)
def execution_detail(
    execution_id: str,
    request: Request,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> ExecutionDetail:
    _authorize(request, x_laya_approval_token)
    try:
        detail = _service(request).execution(execution_id)
    except (ExecutionHistoryError, WorkflowStateError) as exc:
        raise HTTPException(status_code=404, detail={"code": "execution_not_found", "message": "Execution history record is unavailable."}) from exc
    if detail.execution.project_id is not None:
        try:
            request.app.state.project_contexts.resolve(detail.execution.project_id)
        except ProjectContextError as exc:
            raise HTTPException(status_code=404, detail={"code": "execution_not_found", "message": "Execution history record is unavailable."}) from exc
    _check_selected_project(detail.execution.project_id, project_id)
    if detail.workflow is not None:
        _workflow_for_operator(request, detail.workflow.workflow_id, project_id)
    return detail


@router.get("/decisions", response_model=DecisionPage)
def decision_list(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> DecisionPage:
    _authorize(request, x_laya_approval_token)
    return _service(request).decisions(limit=limit, offset=offset, project_ids=_selected_project_ids(request, project_id))


@router.get("/status", response_model=SystemStatusResponse)
def operator_status(
    request: Request,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> SystemStatusResponse:
    _authorize(request, x_laya_approval_token)
    data = _service(request).diagnostics(project_ids=_selected_project_ids(request, project_id))
    system1 = request.app.state.laya_adapter.observation.snapshot(request.app.state.laya_adapter.runtime_configured)
    system2 = request.app.state.system2_adapter.observation.snapshot(request.app.state.system2_adapter.runtime_configured)
    return SystemStatusResponse(**data, system1=RuntimeComponentStatus(**system1.__dict__), system2=RuntimeComponentStatus(**system2.__dict__))
