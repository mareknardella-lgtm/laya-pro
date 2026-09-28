"""Authenticated explicit project memory APIs; memory is context only, never authorization."""

from __future__ import annotations

import hmac
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ...context.project_context import ProjectContextError
from ...memory.manager import MemoryManager, MemoryValidationError
from ...memory.models import MemoryEntry, MemoryScope
from ...security.session import authorize_operator as _auth_op

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


class MemoryWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=1, max_length=128)
    value: Any
    scope: MemoryScope = MemoryScope.TEMPORARY


def _authorize(request: Request, token: Optional[str]) -> None:
    _auth_op(request, token)


def _project(request: Request, project_id: str) -> None:
    try:
        request.app.state.project_contexts.resolve(project_id)
    except ProjectContextError as exc:
        raise HTTPException(status_code=403, detail={"code": "project_not_authorized", "message": str(exc)}) from exc


def _manager(request: Request) -> MemoryManager:
    return request.app.state.memory_manager


@router.post("", response_model=MemoryEntry)
def save_memory(
    body: MemoryWriteRequest,
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> MemoryEntry:
    _authorize(request, x_laya_approval_token)
    _project(request, body.project_id)
    try:
        return _manager(request).put(body.project_id, body.key, body.value, body.scope)
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "memory_value_rejected", "message": str(exc)}) from exc


@router.get("/{project_id}", response_model=list[MemoryEntry])
def list_memory(
    project_id: str,
    request: Request,
    scope: MemoryScope = MemoryScope.PERSISTENT,
    limit: int = 100,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> list[MemoryEntry]:
    _authorize(request, x_laya_approval_token)
    _project(request, project_id)
    return _manager(request).list(project_id, scope, limit)


@router.get("/{project_id}/{key}", response_model=Optional[MemoryEntry])
def get_memory(
    project_id: str,
    key: str,
    request: Request,
    scope: MemoryScope = MemoryScope.PERSISTENT,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> Optional[MemoryEntry]:
    _authorize(request, x_laya_approval_token)
    _project(request, project_id)
    try:
        return _manager(request).get(project_id, key, scope)
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "memory_key_rejected", "message": str(exc)}) from exc


@router.delete("/{project_id}/{key}")
def delete_memory(
    project_id: str,
    key: str,
    request: Request,
    scope: MemoryScope = MemoryScope.PERSISTENT,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> dict[str, bool]:
    _authorize(request, x_laya_approval_token)
    _project(request, project_id)
    try:
        return {"deleted": _manager(request).delete(project_id, key, scope)}
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "memory_key_rejected", "message": str(exc)}) from exc
