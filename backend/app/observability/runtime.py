"""Runtime health counters shared by both system adapters."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RuntimeObservation:
    """Fail-closed telemetry: it records what was attempted, never what was faked."""

    name: str
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: int = 0
    total_failure_latency_ms: int = 0
    last_error: Optional[str] = None
    # Reentrant: snapshot() reads average_latency_ms while already holding the lock.
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def attempted(self) -> None:
        with self._lock:
            self.attempts += 1

    def succeeded(self, latency_ms: int) -> None:
        with self._lock:
            self.successes += 1
            self.total_latency_ms += max(0, latency_ms)

    def failed(self, error: BaseException, latency_ms: int) -> None:
        # Failure latency is tracked separately so the reported average stays
        # the mean latency of calls that actually succeeded.
        with self._lock:
            self.failures += 1
            self.total_failure_latency_ms += max(0, latency_ms)
            self.last_error = f"{type(error).__name__}: {error}"

    @property
    def average_latency_ms(self) -> Optional[int]:
        with self._lock:
            if self.successes == 0:
                return None
            return int(self.total_latency_ms / self.successes)

    @property
    def average_failure_latency_ms(self) -> Optional[int]:
        with self._lock:
            if self.failures == 0:
                return None
            return int(self.total_failure_latency_ms / self.failures)

    def snapshot(self) -> dict:
        """Telemetry only.

        Deliberately omits 'configured': that flag belongs to the adapter that
        knows about the runtime, and duplicating it here let a stale true
        overwrite the real answer on /status.
        """

        with self._lock:
            return {
                "name": self.name,
                "attempts": self.attempts,
                "successes": self.successes,
                "failures": self.failures,
                "average_latency_ms": self.average_latency_ms,
                "average_failure_latency_ms": self.average_failure_latency_ms,
                "last_error": self.last_error,
            }