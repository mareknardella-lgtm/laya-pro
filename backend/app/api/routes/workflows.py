"""Versioned local workflow routes."""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ...context.project_context import ProjectContextError
from ...workflows.engine import WorkflowEngine, WorkflowError
from ...workflows.models import WorkflowDefinition, WorkflowRecord
from ...workflows.state import WorkflowStateError
from ...security.approval_gate import ApprovalError
from ...security.session import authorize_operator as _auth_op

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


class WorkflowCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: WorkflowDefinition
    run_immediately: bool = False


class WorkflowResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_ids: dict[str, str] = Field(default_factory=dict)


def _operator(request: Request, token: Optional[str]) -> str:
    return _auth_op(request, token)


def _record(request: Request, workflow_id: str) -> WorkflowRecord:
    engine: WorkflowEngine = request.app.state.workflow_engine
    try:
        record = engine.get(workflow_id)
    except WorkflowStateError as exc:
        raise HTTPException(status_code=404, detail={"code": "workflow_not_found", "message": str(exc)}) from exc
    try:
        request.app.state.project_contexts.resolve(record.project_id)
    except ProjectContextError as exc:
        raise HTTPException(status_code=403, detail={"code": "project_not_authorized", "message": str(exc)}) from exc
    return record


@router.post("", response_model=WorkflowRecord, status_code=201)
async def create_workflow(
    body: WorkflowCreateRequest,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> WorkflowRecord:
    _operator(request, x_laya_approval_token)
    engine: WorkflowEngine = request.app.state.workflow_engine
    try:
        record = engine.create(body.definition)
        if body.run_immediately:
            record = await engine.run(record.workflow_id)
        return record
    except WorkflowError as exc:
        raise HTTPException(status_code=422, detail={"code": "workflow_invalid", "message": str(exc)}) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail={"code": "approval_conflict", "message": str(exc)}) from exc


@router.get("", response_model=list[WorkflowRecord])
def list_workflows(
    request: Request,
    limit: int = 100,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> list[WorkflowRecord]:
    _operator(request, x_laya_approval_token)
    engine: WorkflowEngine = request.app.state.workflow_engine
    results: list[WorkflowRecord] = []
    for record in engine.list(limit):
        try:
            request.app.state.project_contexts.resolve(record.project_id)
        except ProjectContextError:
            continue
        results.append(record)
    return results


@router.get("/{workflow_id}", response_model=WorkflowRecord)
def get_workflow(
    workflow_id: str,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> WorkflowRecord:
    _operator(request, x_laya_approval_token)
    return _record(request, workflow_id)


@router.post("/{workflow_id}/pause", response_model=WorkflowRecord)
async def pause_workflow(
    workflow_id: str,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> WorkflowRecord:
    _operator(request, x_laya_approval_token)
    _record(request, workflow_id)
    try:
        return await request.app.state.workflow_engine.pause(workflow_id)
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail={"code": "workflow_conflict", "message": str(exc)}) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail={"code": "approval_conflict", "message": str(exc)}) from exc


@router.post("/{workflow_id}/resume", response_model=WorkflowRecord)
async def resume_workflow(
    workflow_id: str,
    request: Request,
    body: WorkflowResumeRequest,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> WorkflowRecord:
    _operator(request, x_laya_approval_token)
    _record(request, workflow_id)
    try:
        return await request.app.state.workflow_engine.resume(workflow_id, body.approval_ids)
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail={"code": "workflow_conflict", "message": str(exc)}) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail={"code": "approval_conflict", "message": str(exc)}) from exc


@router.post("/{workflow_id}/cancel", response_model=WorkflowRecord)
async def cancel_workflow(
    workflow_id: str,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> WorkflowRecord:
    _operator(request, x_laya_approval_token)
    _record(request, workflow_id)
    try:
        return await request.app.state.workflow_engine.cancel(workflow_id)
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail={"code": "workflow_conflict", "message": str(exc)}) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail={"code": "approval_conflict", "message": str(exc)}) from exc
