"""Conservative projection of action parameters for the local operator UI."""

from __future__ import annotations

import re
from typing import Any


_SECRET_KEY = re.compile(r"(password|secret|token|credential|api[_-]?key|private[_-]?key|authorization)", re.IGNORECASE)
_SECRET_TEXT = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/=-]{12,}|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b)",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT = re.compile(
    r"\b(password|secret|token|api[_-]?key|credential|authorization)\s*[:=]\s*(\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)


def safe_text(value: str, max_length: int = 256) -> str:
    """Bound operator-facing text and redact common credential assignments/tokens."""
    text = value[: max(0, max_length)]
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return _SECRET_TEXT.sub("[REDACTED: credential-like value]", text)


def safe_parameters(value: Any, depth: int = 0) -> Any:
    """Return bounded display data; never reveal text-file contents or recognizable tokens."""
    if depth > 8:
        return "[omitted: nesting limit]"
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, child in list(value.items())[:64]:
            name = str(key)[:128]
            normalized = name.lower().replace("-", "_")
            if _SECRET_KEY.search(normalized):
                safe[name] = "[REDACTED]"
            elif normalized in {"content", "prompt", "user_input", "text", "body"} and isinstance(child, str):
                safe[name] = "[OMITTED: content is not exposed in operator previews]"
            else:
                safe[name] = safe_parameters(child, depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        return [safe_parameters(item, depth + 1) for item in value[:64]]
    if isinstance(value, str):
        text = value[:256]
        if _SECRET_TEXT.search(text):
            return "[REDACTED: credential-like value]"
        return _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[OMITTED: unsupported value]"
