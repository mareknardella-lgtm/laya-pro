"""Local operator session management with loopback and same-origin protections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
import ipaddress
import secrets
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request


@dataclass
class OperatorSession:
    session_token: str
    created_at: datetime
    expires_at: datetime
    client_host: str


class OperatorSessionManager:
    """In-memory session registry for local operator consent."""

    def __init__(self, default_ttl_seconds: int = 86_400) -> None:
        self._default_ttl_seconds = default_ttl_seconds
        self._sessions: dict[str, OperatorSession] = {}

    def create_session(self, client_host: str = "127.0.0.1", ttl_seconds: Optional[int] = None) -> OperatorSession:
        self.cleanup_expired()
        ttl = ttl_seconds or self._default_ttl_seconds
        token = f"laya_sess_{secrets.token_urlsafe(32)}"
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl)
        session = OperatorSession(
            session_token=token,
            created_at=now,
            expires_at=expires_at,
            client_host=client_host,
        )
        self._sessions[token] = session
        return session

    def validate_session(self, token: Optional[str]) -> bool:
        if not token or token not in self._sessions:
            return False
        session = self._sessions[token]
        if session.expires_at <= datetime.now(timezone.utc):
            self._sessions.pop(token, None)
            return False
        return True

    def revoke_session(self, token: Optional[str]) -> bool:
        if token and token in self._sessions:
            del self._sessions[token]
            return True
        return False

    def cleanup_expired(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [token for token, session in self._sessions.items() if session.expires_at <= now]
        for token in expired:
            self._sessions.pop(token, None)


def is_loopback_host(host_str: Optional[str]) -> bool:
    """Return True if host is a recognized local loopback hostname or IP."""
    if not host_str:
        return False
    clean_host = host_str.split(":")[0].strip().lower()
    if clean_host in {"localhost", "testserver", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(clean_host).is_loopback
    except ValueError:
        return False


def verify_local_origin(request: Request) -> None:
    """Guard against cross-origin attacks (DNS rebinding, CSRF, malicious embedding)."""
    client_host = request.client.host if request.client else None
    if client_host and not is_loopback_host(client_host):
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden_client", "message": "Only local loopback clients may establish an operator session."}
        )

    host_header = request.headers.get("host")
    if host_header and not is_loopback_host(host_header):
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden_host", "message": "Host header must target a local loopback interface."}
        )

    origin_header = request.headers.get("origin")
    if origin_header:
        parsed_origin = urlparse(origin_header)
        if not is_loopback_host(parsed_origin.hostname):
            raise HTTPException(
                status_code=403,
                detail={"code": "forbidden_origin", "message": f"Cross-origin request from {origin_header} is forbidden."}
            )

    referer_header = request.headers.get("referer")
    if referer_header:
        parsed_referer = urlparse(referer_header)
        if not is_loopback_host(parsed_referer.hostname):
            raise HTTPException(
                status_code=403,
                detail={"code": "forbidden_referer", "message": "Referer must originate from a local loopback interface."}
            )

    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site and fetch_site.lower() == "cross-site":
        raise HTTPException(
            status_code=403,
            detail={"code": "cross_site_blocked", "message": "Cross-site requests to local operator sessions are blocked."}
        )


def authorize_operator(request: Request, token: Optional[str]) -> str:
    """Authorize either the configured master approval token or a valid local session token."""
    # 1. Validation against active in-memory session store (consented operator session)
    session_mgr: Optional[OperatorSessionManager] = getattr(request.app.state, "session_manager", None)
    if session_mgr is not None and session_mgr.validate_session(token):
        return "local-operator"

    # 2. Constant-time comparison with configured master approval token
    configured = request.app.state.settings.approval_token
    if configured is not None and token is not None and hmac.compare_digest(token, configured):
        return "local-operator"

    if configured is None and (session_mgr is None or not token):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "operator_auth_not_configured",
                "message": "Configure the local operator token before opening the control center."
            }
        )

    raise HTTPException(
        status_code=401,
        detail={
            "code": "operator_authentication_required",
            "message": "A valid local operator token is required."
        }
    )
