"""Tests for runtime observability counters."""

from __future__ import annotations

import threading

from backend.app.observability.runtime import RuntimeObservation


def test_snapshot_does_not_deadlock_on_nested_locking():
    """snapshot() reads a property that locks again: the lock must be reentrant."""

    observation = RuntimeObservation("test")
    observation.attempted()
    observation.succeeded(120)
    observation.attempted()
    observation.failed(RuntimeError("boom"), 30)

    snapshot: dict = {}
    thread = threading.Thread(target=lambda: snapshot.update(observation.snapshot()))
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive(), "snapshot() deadlocked"
    assert snapshot["attempts"] == 2
    assert snapshot["successes"] == 1
    assert snapshot["failures"] == 1
    assert snapshot["average_latency_ms"] == 120
    assert snapshot["average_failure_latency_ms"] == 30
    assert "boom" in snapshot["last_error"]


def test_average_latency_is_none_before_any_success():
    assert RuntimeObservation("test").snapshot()["average_latency_ms"] is None