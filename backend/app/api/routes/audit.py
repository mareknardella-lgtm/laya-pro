"""Read-only, authenticated audit event endpoint."""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request

from ...observability.audit import AuditLog
from ...security.session import authorize_operator as _auth_op

router = APIRouter(prefix="/api/v1", tags=["audit"])


@router.get("/audit")
def list_audit(
    request: Request,
    limit: int = 100,
    event_type: Optional[str] = None,
    project_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> list[dict]:
    _auth_op(request, x_laya_approval_token)
    audit: AuditLog = request.app.state.audit_log
    authorized_projects = request.app.state.project_contexts.project_ids
    project_ids = sorted(authorized_projects) if project_id is None else ([project_id] if project_id in authorized_projects else [])
    return audit.list_events(limit=limit, event_type=event_type, project_ids=project_ids)
