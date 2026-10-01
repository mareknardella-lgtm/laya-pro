"""Models for long-term memory entries."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class MemoryScope(str, Enum):
    """Only durable, cross-session facts live in memory today.

    SESSION-scoped entries would need retrieval to filter by session_id and
    expiration to drop them; neither exists, so the scope is not advertised.
    """

    PERSISTENT = "persistent"


class MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=4_000)
    scope: MemoryScope = MemoryScope.PERSISTENT
    pinned: bool = False
    """A fact the user added on purpose: always eligible for the prompt, whatever
    the question looks like. Extracted facts are matched by keywords instead."""
    updated_at: Optional[str] = None