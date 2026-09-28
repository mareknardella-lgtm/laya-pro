"""Controlled project file read and replacement operations."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..sandbox.manager import SandboxManager, SandboxViolation
from .backup import BackupManager, BackupSnapshot
from .rollback import ChangeRecord, FileOperationError, RollbackManager


@dataclass(frozen=True)
class PatchResult:
    operation_id: str
    project_id: str
    relative_path: str
    before_sha256: str
    after_sha256: str
    backup_id: str
    bytes_written: int
    status: str


class PatchEngine:
    def __init__(
        self,
        sandbox: SandboxManager,
        backup: BackupManager,
        rollback: RollbackManager,
        max_file_bytes: int = 1_048_576,
    ) -> None:
        self._sandbox = sandbox
        self._backup = backup
        self._rollback = rollback
        self._max_file_bytes = max_file_bytes

    def read(self, project_id: str, relative_path: str) -> bytes:
        return self._sandbox.read_bytes(project_id, relative_path)

    def replace(
        self,
        project_id: str,
        relative_path: str,
        new_content: str,
        expected_sha256: Optional[str] = None,
    ) -> PatchResult:
        if not isinstance(new_content, str):
            raise FileOperationError("Text replacement content must be a string")
        data = new_content.encode("utf-8")
        if len(data) > self._max_file_bytes:
            raise FileOperationError("Replacement exceeds configured file size limit")
        target = self._sandbox.resolve(project_id, relative_path)
        if target.exists() and not target.is_file():
            raise FileOperationError("Only regular files can be modified")
        current = target.read_bytes() if target.exists() else b""
        if not target.exists() and expected_sha256 is not None:
            expected_absent = hashlib.sha256(b"").hexdigest()
            if expected_sha256 != expected_absent:
                raise FileOperationError("File does not exist and the expected digest is not the empty-file digest")
        if len(current) > self._max_file_bytes:
            raise FileOperationError("Existing file exceeds configured file size limit")
        before_digest = hashlib.sha256(current).hexdigest()
        if expected_sha256 is not None and expected_sha256 != before_digest:
            raise FileOperationError("File changed since it was reviewed; expected digest does not match")
        if target.exists() and target.is_symlink():
            raise SandboxViolation("Symlink targets cannot be replaced")
        snapshot = self._backup.snapshot(target, project_id, relative_path)
        after_digest = hashlib.sha256(data).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        self._sandbox.resolve(project_id, relative_path)
        if target.exists() and target.is_symlink():
            raise SandboxViolation("Symlink targets cannot be replaced")
        temporary_name: Optional[str] = None
        try:
            fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".patch", dir=str(target.parent))
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            current_now = target.read_bytes() if target.exists() else b""
            if hashlib.sha256(current_now).hexdigest() != before_digest:
                raise FileOperationError("File changed while the patch was being prepared")
            os.replace(temporary_name, target)
            temporary_name = None
        except Exception:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)
            raise
        try:
            operation = self._rollback.record_operation(project_id, relative_path, snapshot, after_digest)
        except Exception:
            # A file change without a durable rollback record would be unsafe; restore immediately.
            if snapshot.existed:
                original = self._backup.restore_bytes(snapshot)
                with tempfile.NamedTemporaryFile(dir=str(target.parent), delete=False) as stream:
                    recovery_path = Path(stream.name)
                    stream.write(original)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(str(recovery_path), str(target))
            else:
                target.unlink(missing_ok=True)
            raise
        return PatchResult(
            operation_id=operation.operation_id,
            project_id=project_id,
            relative_path=relative_path,
            before_sha256=before_digest,
            after_sha256=after_digest,
            backup_id=snapshot.backup_id,
            bytes_written=len(data),
            status="applied",
        )
