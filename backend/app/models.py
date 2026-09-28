"""Shared Pydantic API contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HealthResponse(StrictModel):
    status: str = "ok"
    service: str = "laya-pro"
    mode: str = "local"


class ErrorResponse(StrictModel):
    code: str
    message: str
    request_id: Optional[str] = None


class RequestContext(StrictModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=utc_now)
