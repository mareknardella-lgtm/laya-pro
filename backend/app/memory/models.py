"""Explicit project memory contracts; stored context never carries execution authority."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MemoryScope(str, Enum):
    TEMPORARY = "temporary"
    PERSISTENT = "persistent"


class MemorySensitivity(str, Enum):
    NORMAL = "normal"


class MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    key: str = Field(min_length=1, max_length=128)
    value: Any
    scope: MemoryScope
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL
    created_at: datetime
    updated_at: datetime
