"""Explicitly coded and schema-validated project file tools."""

from __future__ import annotations

import hashlib
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ...changes.patch_engine import PatchEngine
from ...changes.rollback import FileOperationError, RollbackManager
from ...core.execution_context import ExecutionContext
from ...sandbox.manager import SandboxManager, SandboxViolation
from ..models import ExecutionMode, RiskLevel
from ..registry import ToolDefinition, ToolRegistry


class FileReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=1_024)


class FileReadOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str
    sha256: str
    size_bytes: int


class FileReplaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    path: str = Field(min_length=1, max_length=1_024)
    content: str = Field(max_length=16_777_216)
    expected_sha256: Optional[str] = Field(default=None, min_length=64, max_length=64)


class FileReplaceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    project_id: str
    path: str
    before_sha256: str
    after_sha256: str
    backup_id: str
    bytes_written: int
    status: str


class FileRollbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation_id: str = Field(min_length=1, max_length=128)


class FileRollbackOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    project_id: str
    path: str
    status: str


def file_action_reference(definition: ToolDefinition, parameters: dict, raw_context: object) -> Optional[str]:
    if definition.tool_id != "file.rollback":
        return None
    _require_context(raw_context)
    return parameters.get("operation_id")


def register_filesystem_tools(
    registry: ToolRegistry,
    sandbox: SandboxManager,
    patch_engine: PatchEngine,
    rollback_manager: RollbackManager,
    max_timeout_seconds: float = 10.0,
) -> None:
    async def read_file(params: BaseModel, raw_context: object) -> BaseModel:
        context = _require_context(raw_context)
        if context.project is None:
            raise SandboxViolation("Project context is required")
        data = patch_engine.read(context.project.project_id, params.path)
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FileOperationError("File is not valid UTF-8 text") from exc
        return FileReadOutput(
            path=params.path,
            content=content,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )

    async def replace_file(params: BaseModel, raw_context: object) -> BaseModel:
        context = _require_context(raw_context)
        if context.project is None:
            raise SandboxViolation("Project context is required")
        result = patch_engine.replace(
            context.project.project_id,
            params.path,
            params.content,
            expected_sha256=params.expected_sha256,
        )
        return FileReplaceOutput(
            operation_id=result.operation_id,
            project_id=result.project_id,
            path=result.relative_path,
            before_sha256=result.before_sha256,
            after_sha256=result.after_sha256,
            backup_id=result.backup_id,
            bytes_written=result.bytes_written,
            status=result.status,
        )

    async def rollback_file(params: BaseModel, raw_context: object) -> BaseModel:
        context = _require_context(raw_context)
        if context.project is None:
            raise SandboxViolation("Project context is required")
        record = rollback_manager.get_record(params.operation_id)
        if record.project_id != context.project.project_id:
            raise SandboxViolation("Rollback record belongs to another project")
        target = sandbox.resolve(record.project_id, record.relative_path)
        updated = rollback_manager.rollback(record.operation_id, target)
        return FileRollbackOutput(
            operation_id=updated.operation_id,
            project_id=updated.project_id,
            path=updated.relative_path,
            status=updated.status,
        )

    registry.register(ToolDefinition(
        tool_id="file.read",
        description="Read a bounded UTF-8 file inside the explicitly authorized project root.",
        input_model=FileReadInput,
        output_model=FileReadOutput,
        handler=read_file,
        risk_level=RiskLevel.LOW,
        required_permissions=frozenset({"project:read"}),
        timeout_seconds=max_timeout_seconds,
        execution_mode=ExecutionMode.INLINE_TRUSTED,
        prerequisites=("project",),
        cancellable=True,
    ))
    registry.register(ToolDefinition(
        tool_id="file.replace",
        description="Atomically replace a UTF-8 text file after explicit approval and verified backup.",
        input_model=FileReplaceInput,
        output_model=FileReplaceOutput,
        handler=replace_file,
        risk_level=RiskLevel.HIGH,
        required_permissions=frozenset({"project:write"}),
        timeout_seconds=max_timeout_seconds,
        execution_mode=ExecutionMode.INLINE_TRUSTED,
        prerequisites=("project",),
        cancellable=False,
    ))
    registry.register(ToolDefinition(
        tool_id="file.rollback",
        description="Restore a specific prior file snapshot if the current content has not changed.",
        input_model=FileRollbackInput,
        output_model=FileRollbackOutput,
        handler=rollback_file,
        risk_level=RiskLevel.HIGH,
        required_permissions=frozenset({"project:write"}),
        timeout_seconds=max_timeout_seconds,
        execution_mode=ExecutionMode.INLINE_TRUSTED,
        prerequisites=("project",),
        cancellable=False,
    ))


def _require_context(raw_context: object) -> ExecutionContext:
    if not isinstance(raw_context, ExecutionContext):
        raise SandboxViolation("Execution context is required")
    return raw_context
