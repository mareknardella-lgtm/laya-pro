"""Diagnostics router for real runtime connectivity inspection.

Reports detailed, non-sensitive diagnostics for both System 1 (Laya AI) and
System 2 (NVIDIA AI) distinguishing:
- not_configured
- unverified
- auth_failed
- unreachable
- operational

Credentials, raw secrets, and sensitive tokens are strictly masked.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from ...security.session import authorize_operator as _auth_op

router = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])


class ServiceDiagnosticDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: str
    configured: bool
    status: str
    endpoint: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    last_error_code: Optional[str] = None
    operational: bool = False


class WatchdogDiagnosticDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    running: bool
    system_down: bool
    consecutive_failures: int


class DiagnosticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system1_laya: ServiceDiagnosticDetail
    system2_generative: ServiceDiagnosticDetail
    watchdog: Optional[WatchdogDiagnosticDetail] = None


def _authorize_operator(request: Request, token: Optional[str]) -> None:
    _auth_op(request, token)


@router.get("", response_model=DiagnosticsResponse)
def get_diagnostics(
    request: Request,
    x_laya_approval_token: Optional[str] = Header(default=None),
) -> DiagnosticsResponse:
    _authorize_operator(request, x_laya_approval_token)
    settings = request.app.state.settings
    laya_obs = request.app.state.laya_adapter.observation
    s2_obs = request.app.state.system2_adapter.observation

    # System 1 status evaluation
    s1_configured = request.app.state.laya_adapter.runtime_configured
    if settings.laya_decision_url:
        s1_endpoint = settings.laya_decision_url
        s1_provider = "local-laya-serve"
        s1_model = "laya-decision-engine"
    elif settings.laya_wrapper_path:
        s1_endpoint = str(settings.laya_wrapper_path)
        s1_provider = "local-torch-wrapper"
        s1_model = "laya-multilingual"
    else:
        s1_endpoint = None
        s1_provider = "local-laya"
        s1_model = "laya-decision-engine"

    if not s1_configured:
        s1_status = "not_configured"
    elif not laya_obs._has_attempt:
        s1_status = "unverified"
    elif laya_obs._last_attempt_succeeded:
        s1_status = "operational"
    elif laya_obs._last_error_code == "LayaAuthError":
        s1_status = "auth_failed"
    else:
        s1_status = "unreachable"

    s1_detail = ServiceDiagnosticDetail(
        name="Laya AI (System 1)",
        role="Authoritative decision proposals & action routing",
        configured=s1_configured,
        status=s1_status,
        endpoint=s1_endpoint,
        provider=s1_provider,
        model=s1_model,
        last_error_code=laya_obs._last_error_code,
        operational=(s1_status == "operational"),
    )

    # System 2 status evaluation
    s2_client = request.app.state.system2_adapter._client
    s2_configured = s2_client.is_configured
    active_provider = settings.active_system2_provider
    if not s2_configured:
        s2_status = "not_configured"
    elif not s2_obs._has_attempt:
        s2_status = "unverified"
    elif s2_obs._last_attempt_succeeded:
        s2_status = "operational"
    elif s2_obs._last_error_code in {"System2AuthError"}:
        s2_status = "auth_failed"
    elif s2_obs._last_error_code in {"System2RateLimitError"}:
        s2_status = "rate_limited"
    else:
        s2_status = "unreachable"

    endpoint_masked = settings.nvidia_ai_base_url if active_provider == "nvidia" else settings.system2_generate_url
    model_name = settings.nvidia_ai_model if active_provider == "nvidia" else "default-plain-text"

    s2_detail = ServiceDiagnosticDetail(
        name="Generative Assistant (System 2)",
        role="Non-authoritative plain-text generation only",
        configured=s2_configured,
        status=s2_status,
        endpoint=endpoint_masked,
        provider=active_provider,
        model=model_name,
        last_error_code=s2_obs._last_error_code,
        operational=(s2_status == "operational"),
    )

    watchdog_detail = None
    watchdog = getattr(request.app.state, "watchdog", None)
    if watchdog is not None:
        status = watchdog.get_status()
        watchdog_detail = WatchdogDiagnosticDetail(
            running=status["running"],
            system_down=status["system_down"],
            consecutive_failures=status["consecutive_failures"],
        )

    return DiagnosticsResponse(
        system1_laya=s1_detail,
        system2_generative=s2_detail,
        watchdog=watchdog_detail,
    )
