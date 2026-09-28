"""Content-addressed file backups; backup files are stored outside authorized project roots."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4


class BackupIntegrityError(IOError):
    """Backup does not match its expected digest or metadata."""


@dataclass(frozen=True)
class BackupSnapshot:
    backup_id: str
    project_id: str
    relative_path: str
    existed: bool
    content_sha256: str
    size_bytes: int
    storage_path: Path


class BackupManager:
    PROJECT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

    def __init__(self, storage_root: Path, max_backup_bytes: int = 16_777_216) -> None:
        self._root = storage_root.expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_backup_bytes = max_backup_bytes

    def snapshot(self, target: Path, project_id: str = "default", relative_path: Optional[str] = None) -> BackupSnapshot:
        if not self.PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("Invalid project identifier")
        exists = target.exists()
        if exists and (target.is_symlink() or not target.is_file()):
            raise BackupIntegrityError("Only regular files can be backed up")
        data = target.read_bytes() if exists else b""
        if len(data) > self._max_backup_bytes:
            raise BackupIntegrityError("File is too large to back up")
        digest = hashlib.sha256(data).hexdigest()
        backup_id = str(uuid4())
        directory = (self._root / project_id).resolve()
        if self._root not in directory.parents:
            raise BackupIntegrityError("Backup directory escaped its configured root")
        directory.mkdir(parents=True, exist_ok=True)
        storage = directory / f"{backup_id}.bin"
        if exists:
            fd, temporary_name = tempfile.mkstemp(prefix=".backup-", dir=str(directory))
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, storage)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        return BackupSnapshot(
            backup_id=backup_id,
            project_id=project_id,
            relative_path=relative_path or target.name,
            existed=exists,
            content_sha256=digest,
            size_bytes=len(data),
            storage_path=storage,
        )

    def restore_bytes(self, snapshot: BackupSnapshot) -> bytes:
        expected_directory = (self._root / snapshot.project_id).resolve()
        storage = snapshot.storage_path.resolve()
        if storage.parent != expected_directory or self._root not in expected_directory.parents:
            raise BackupIntegrityError("Backup reference escaped its configured backup root")
        if not snapshot.existed:
            return b""
        data = storage.read_bytes()
        if len(data) != snapshot.size_bytes or hashlib.sha256(data).hexdigest() != snapshot.content_sha256:
            raise BackupIntegrityError("Backup content failed integrity verification")
        return data
