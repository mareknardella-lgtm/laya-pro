"""Memory inspection endpoints."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...memory.models import MemoryEntry, MemoryScope

router = APIRouter(tags=["memory"])


class MemoryQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=10, ge=1, le=50)


class MemoryUpsertRequest(BaseModel):
    """Lets a user pin a fact the extractor never would have thought of."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=4_000)

    @field_validator("key")
    @classmethod
    def key_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Key cannot be blank")
        return value


@router.post("/memory")
async def upsert_memory(payload: MemoryUpsertRequest, request: Request) -> Dict[str, Any]:
    container = request.app.state.container
    existing = await container.memory_store.get(payload.key, MemoryScope.PERSISTENT)
    entry = MemoryEntry(
        key=payload.key, value=payload.value, scope=MemoryScope.PERSISTENT, pinned=True
    )
    await container.memory_store.put(entry)
    stored = await container.memory_store.get(payload.key, MemoryScope.PERSISTENT)
    return {
        "created": existing is None,
        "stored": stored.model_dump() if stored else None,
    }


@router.post("/memory/query")
async def query_memory(payload: MemoryQueryRequest, request: Request) -> Dict[str, Any]:
    container = request.app.state.container
    entries: List[MemoryEntry] = await container.memory.query(payload.query, payload.limit)
    return {
        "query": payload.query,
        "count": len(entries),
        "entries": [entry.model_dump() for entry in entries],
    }


@router.get("/memory")
async def list_memory(request: Request) -> Dict[str, Any]:
    container = request.app.state.container
    entries = await container.memory_store.all(MemoryScope.PERSISTENT)
    return {
        "count": len(entries),
        "entries": [entry.model_dump() for entry in entries],
    }


@router.put("/memory")
async def edit_memory(
    payload: MemoryUpsertRequest, request: Request
) -> Dict[str, Any]:
    """Edit an existing fact. 404 rather than silently creating a new one."""

    container = request.app.state.container
    existing = await container.memory_store.get(payload.key, MemoryScope.PERSISTENT)
    if existing is None:
        raise HTTPException(status_code=404, detail="memory entry not found")
    entry = MemoryEntry(
        key=payload.key, value=payload.value, scope=MemoryScope.PERSISTENT, pinned=True
    )
    await container.memory_store.put(entry)
    stored = await container.memory_store.get(payload.key, MemoryScope.PERSISTENT)
    return {"stored": stored.model_dump() if stored else None}


@router.delete("/memory")
async def delete_memory(
    request: Request, key: str = Query(min_length=1, max_length=200)
) -> Dict[str, Any]:
    container = request.app.state.container
    deleted = await container.memory_store.delete(key)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory entry not found")
    return {"deleted": key}