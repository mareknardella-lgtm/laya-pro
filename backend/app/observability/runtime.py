"""Process-local health observations that retain metadata only, never prompts or raw errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class RuntimeObservationSnapshot:
    configured: bool
    status: str
    last_attempt_at: Optional[datetime]
    last_successful_at: Optional[datetime]
    last_latency_ms: Optional[int]
    last_error_code: Optional[str]
    native_runtime_verified: bool = False


class RuntimeObservation:
    def __init__(self) -> None:
        self._last_attempt_at: Optional[datetime] = None
        self._last_successful_at: Optional[datetime] = None
        self._last_latency_ms: Optional[int] = None
        self._last_error_code: Optional[str] = None
        self._has_attempt = False
        self._last_attempt_succeeded = False

    def attempted(self) -> None:
        self._has_attempt = True
        self._last_attempt_succeeded = False
        self._last_attempt_at = datetime.now(timezone.utc)
        self._last_error_code = None

    def succeeded(self, latency_ms: int) -> None:
        self._has_attempt = True
        self._last_attempt_succeeded = True
        self._last_attempt_at = datetime.now(timezone.utc)
        self._last_successful_at = self._last_attempt_at
        self._last_latency_ms = max(0, latency_ms)
        self._last_error_code = None

    def failed(self, error: Exception, latency_ms: int) -> None:
        self._has_attempt = True
        self._last_attempt_succeeded = False
        self._last_attempt_at = datetime.now(timezone.utc)
        self._last_latency_ms = max(0, latency_ms)
        self._last_error_code = type(error).__name__[:128]

    def snapshot(self, configured: bool) -> RuntimeObservationSnapshot:
        if not configured:
            status = "unavailable"
        elif not self._has_attempt:
            status = "unknown"
        elif self._last_attempt_succeeded:
            status = "healthy"
        elif self._last_successful_at is not None:
            status = "degraded"
        else:
            status = "unavailable"
        return RuntimeObservationSnapshot(
            configured=configured,
            status=status,
            last_attempt_at=self._last_attempt_at,
            last_successful_at=self._last_successful_at,
            last_latency_ms=self._last_latency_ms,
            last_error_code=self._last_error_code,
            native_runtime_verified=False,
        )
