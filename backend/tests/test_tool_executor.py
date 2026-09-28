from __future__ import annotations

import asyncio
import time
from typing import Optional

import pytest
from pydantic import BaseModel, ConfigDict

from backend.app.tools.executor import (
    DuplicateExecutionError,
    ToolAuthorizationError,
    ToolExecutionError,
    ToolExecutor,
    input_fingerprint,
)
from backend.app.tools.models import ExecutionMode, RiskLevel, ToolAuthorization, ToolExecutionStatus
from backend.app.tools.registry import ToolDefinition, ToolRegistry


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: int


def definition(handler, *, timeout=1.0, mode=ExecutionMode.INLINE_TRUSTED) -> ToolDefinition:
    return ToolDefinition(
        tool_id="test.add",
        description="Test-only deterministic operation",
        input_model=Input,
        output_model=Output,
        handler=handler,
        risk_level=RiskLevel.LOW,
        required_permissions=frozenset({"test:run"}),
        timeout_seconds=timeout,
        execution_mode=mode,
    )


async def provider(defn, params, context, fingerprint, approval_id=None, action_ref=None) -> ToolAuthorization:
    return ToolAuthorization(
        tool_id=defn.tool_id,
        input_fingerprint=fingerprint,
        permissions=frozenset({"test:run"}),
        expires_at_epoch=time.time() + 60,
    )


def test_registry_only_exposes_registered_schemas() -> None:
    async def handler(_input: BaseModel, _context: object) -> BaseModel:
        return Output(result=1)

    registry = ToolRegistry()
    registry.register(definition(handler))
    assert registry.list_specs()[0].tool_id == "test.add"
    with pytest.raises(Exception):
        registry.register(definition(handler))
    with pytest.raises(ToolExecutionError):
        asyncio.run(ToolExecutor(registry, provider).execute("model.invented", {}, None, "key"))


def test_executor_requires_authorizer_and_valid_input() -> None:
    async def handler(value: BaseModel, _context: object) -> BaseModel:
        return Output(result=value.value)

    registry = ToolRegistry()
    registry.register(definition(handler))
    with pytest.raises(ToolAuthorizationError):
        asyncio.run(ToolExecutor(registry).execute("test.add", {"value": 1}, None, "key"))
    with pytest.raises(ToolExecutionError):
        asyncio.run(ToolExecutor(registry, provider).execute("test.add", {"value": "bad"}, None, "key"))


def test_executor_validates_authorization_and_runs_registered_handler_once() -> None:
    calls = 0

    async def handler(value: BaseModel, _context: object) -> BaseModel:
        nonlocal calls
        calls += 1
        return Output(result=value.value + 1)

    registry = ToolRegistry()
    registry.register(definition(handler))
    executor = ToolExecutor(registry, provider)

    async def run() -> None:
        first = await executor.execute("test.add", {"value": 4}, None, "idem-1")
        replay = await executor.execute("test.add", {"value": 4}, None, "idem-1")
        assert first.status == ToolExecutionStatus.SUCCEEDED
        assert first.output == {"result": 5}
        assert replay.replayed is True
        assert replay.execution_id == first.execution_id
        assert calls == 1

    asyncio.run(run())


def test_idempotency_key_cannot_be_reused_with_different_input() -> None:
    async def handler(value: BaseModel, _context: object) -> BaseModel:
        return Output(result=value.value)

    registry = ToolRegistry()
    registry.register(definition(handler))
    executor = ToolExecutor(registry, provider)

    async def run() -> None:
        await executor.execute("test.add", {"value": 1}, None, "idem")
        with pytest.raises(DuplicateExecutionError):
            await executor.execute("test.add", {"value": 2}, None, "idem")

    asyncio.run(run())


def test_tool_timeout_is_structured() -> None:
    async def handler(_value: BaseModel, _context: object) -> BaseModel:
        await asyncio.sleep(0.1)
        return Output(result=1)

    registry = ToolRegistry()
    registry.register(definition(handler, timeout=0.01))
    result = asyncio.run(ToolExecutor(registry, provider).execute("test.add", {"value": 1}, None, "timeout"))
    assert result.status == ToolExecutionStatus.TIMED_OUT


def test_isolated_mode_fails_closed_without_sandbox() -> None:
    async def handler(value: BaseModel, _context: object) -> BaseModel:
        return Output(result=value.value)

    registry = ToolRegistry()
    registry.register(definition(handler, mode=ExecutionMode.ISOLATED_PROCESS))
    with pytest.raises(ToolExecutionError):
        asyncio.run(ToolExecutor(registry, provider).execute("test.add", {"value": 1}, None, "isolated"))


def test_authorization_must_match_input_fingerprint() -> None:
    async def handler(value: BaseModel, _context: object) -> BaseModel:
        return Output(result=value.value)

    async def wrong_provider(defn, params, context, fingerprint, approval_id=None, action_ref=None):
        return ToolAuthorization(
            tool_id=defn.tool_id,
            input_fingerprint="0" * 64,
            permissions=frozenset({"test:run"}),
            expires_at_epoch=time.time() + 60,
        )

    registry = ToolRegistry()
    registry.register(definition(handler))
    with pytest.raises(ToolAuthorizationError):
        asyncio.run(ToolExecutor(registry, wrong_provider).execute("test.add", {"value": 1}, None, "wrong"))
