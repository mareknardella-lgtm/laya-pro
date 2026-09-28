"""Central in-memory tool registry; model output can only reference registered IDs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Type

from pydantic import BaseModel

from .models import ExecutionMode, RiskLevel, ToolSpec

TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
ToolHandler = Callable[[BaseModel, object], Awaitable[BaseModel]]


class ToolRegistryError(ValueError):
    """A tool definition is invalid or not registered."""


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    description: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]
    handler: ToolHandler
    risk_level: RiskLevel
    required_permissions: frozenset[str]
    timeout_seconds: float
    execution_mode: ExecutionMode = ExecutionMode.INLINE_TRUSTED
    prerequisites: tuple[str, ...] = ()
    cancellable: bool = True

    def __post_init__(self) -> None:
        if not TOOL_ID_RE.fullmatch(self.tool_id):
            raise ToolRegistryError("tool_id must be a lowercase identifier")
        if not self.description.strip() or len(self.description) > 1_000:
            raise ToolRegistryError("Tool description must be non-empty and bounded")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 120:
            raise ToolRegistryError("Tool timeout must be between 0 and 120 seconds")
        if not callable(self.handler):
            raise ToolRegistryError("Tool handler must be explicitly registered callable code")
        if not issubclass(self.input_model, BaseModel) or not issubclass(self.output_model, BaseModel):
            raise ToolRegistryError("Tool input and output must use Pydantic models")
        if self.input_model.model_config.get("extra") != "forbid":
            raise ToolRegistryError("Tool input models must forbid unknown fields")
        if self.output_model.model_config.get("extra") != "forbid":
            raise ToolRegistryError("Tool output models must forbid unknown fields")

    def public_spec(self) -> ToolSpec:
        return ToolSpec(
            tool_id=self.tool_id,
            description=self.description,
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
            risk_level=self.risk_level,
            required_permissions=self.required_permissions,
            timeout_seconds=self.timeout_seconds,
            execution_mode=self.execution_mode,
            prerequisites=self.prerequisites,
            cancellable=self.cancellable,
        )


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.tool_id in self._definitions:
            raise ToolRegistryError(f"Tool {definition.tool_id!r} is already registered")
        self._definitions[definition.tool_id] = definition

    def get(self, tool_id: str) -> ToolDefinition:
        try:
            return self._definitions[tool_id]
        except KeyError as exc:
            raise ToolRegistryError("Tool is not registered") from exc

    def list_specs(self) -> list[ToolSpec]:
        return [self._definitions[key].public_spec() for key in sorted(self._definitions)]

    def __len__(self) -> int:
        return len(self._definitions)
