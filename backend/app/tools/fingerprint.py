"""Stable action fingerprint shared by policy, approval, and execution boundaries."""

from __future__ import annotations

import hashlib
import json
from typing import Optional


def input_fingerprint(
    tool_id: str,
    parameters: dict,
    project_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
) -> str:
    payload = json.dumps(
        {
            "tool_id": tool_id,
            "parameters": parameters,
            "project_id": project_id,
            "workflow_id": workflow_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
