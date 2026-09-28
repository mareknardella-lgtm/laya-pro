"""Redaction helpers for structured metadata; raw action payloads must not be logged."""

from __future__ import annotations

from typing import Any

from ..observability.audit import _redact


def redact(value: Any) -> Any:
    return _redact(value)
