"""Path-level filesystem confinement for explicitly authorized project roots.

This is not an operating-system process sandbox and does not make untrusted code safe.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional


class SandboxViolation(PermissionError):
    """A filesystem request escaped or bypassed the configured project boundary."""


class SandboxManager:
    def __init__(self, project_roots: dict[str, Path], max_file_bytes: int = 1_048_576) -> None:
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")
        self._roots = {key: path.expanduser().resolve() for key, path in project_roots.items()}
        self._max_file_bytes = max_file_bytes

    def resolve(self, project_id: str, relative_path: str, *, must_exist: bool = False) -> Path:
        root = self._roots.get(project_id)
        if root is None:
            raise SandboxViolation("Project root is not configured")
        if not relative_path or "\x00" in relative_path:
            raise SandboxViolation("A non-empty relative path is required")
        candidate_text = relative_path.replace("\\", "/")
        candidate = Path(candidate_text)
        windows_parts = candidate_text.split("/")
        if (candidate.is_absolute() or candidate.drive or "\\:" in candidate_text
                or any(part in {"..", ""} for part in windows_parts)
                or any(len(part) >= 2 and part[1] == ":" for part in windows_parts)):

            raise SandboxViolation("Absolute paths and traversal components are forbidden")
        candidate = Path(*windows_parts)
        lexical = root.joinpath(candidate)
        current = root
        for part in candidate.parts:
            if part in {".", ""}:
                continue
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            if getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise SandboxViolation("Windows reparse points are not allowed in project paths")
            if current.is_symlink():
                raise SandboxViolation("Symlink paths are not allowed for project operations")
        resolved = lexical.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise SandboxViolation("Resolved path is outside the authorized project root")
        if must_exist and not resolved.exists():
            raise FileOperationNotFound("Requested project file does not exist")
        return resolved

    def read_bytes(self, project_id: str, relative_path: str) -> bytes:
        path = self.resolve(project_id, relative_path, must_exist=True)
        if not path.is_file():
            raise SandboxViolation("Only regular files can be read")
        size = path.stat().st_size
        if size > self._max_file_bytes:
            raise SandboxViolation("File exceeds the configured size limit")
        with path.open("rb") as stream:
            data = stream.read(self._max_file_bytes + 1)
        if len(data) > self._max_file_bytes:
            raise SandboxViolation("File exceeds the configured size limit")
        return data


class FileOperationNotFound(FileNotFoundError):
    """An authorized file was not found under its project root."""
