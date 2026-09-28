"""Conflict-aware rollback registry with SQLite metadata and verified content backups."""

from __future__ import annotations

import hashlib
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4
import os
from uuid import uuid4

from ..storage.sqlite import SQLiteDatabase
from ..observability.audit import AuditEvent, AuditLog
from ..tools.fingerprint import input_fingerprint
from .backup import BackupIntegrityError, BackupManager, BackupSnapshot


class FileOperationError(RuntimeError):
    """A controlled file operation could not safely be completed."""


@dataclass(frozen=True)
class ChangeRecord:
    operation_id: str
    project_id: str
    relative_path: str
    backup_id: str
    existed_before: bool
    before_sha256: str
    after_sha256: str
    status: str


class RollbackManager:
    def __init__(self, backup_manager: BackupManager, database: Optional[SQLiteDatabase] = None, audit: Optional[AuditLog] = None) -> None:
        self._backups = backup_manager
        self._database = database
        self._audit = audit
        self._memory: dict[str, tuple[ChangeRecord, BackupSnapshot]] = {}

    def record_operation(self, project_id: str, relative_path: str, snapshot: BackupSnapshot, after_sha256: str) -> ChangeRecord:
        operation = ChangeRecord(
            operation_id=str(uuid4()),
            project_id=project_id,
            relative_path=relative_path,
            backup_id=snapshot.backup_id,
            existed_before=snapshot.existed,
            before_sha256=snapshot.content_sha256,
            after_sha256=after_sha256,
            status="applied",
        )
        self._memory[operation.operation_id] = (operation, snapshot)
        if self._database:
            with self._database.transaction() as connection:
                connection.execute(
                    """INSERT INTO change_records
                    (operation_id, project_id, relative_path, backup_id, existed_before, before_sha256, after_sha256, size_bytes, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        operation.operation_id, project_id, relative_path, snapshot.backup_id,
                        1 if snapshot.existed else 0, snapshot.content_sha256, after_sha256,
                        snapshot.size_bytes, "applied", datetime.now(timezone.utc).isoformat(),
                    ),
                )
        if self._audit:
            self._audit.record(AuditEvent(
                actor_id="system:patch-engine",
                event_type="file.changed",
                project_id=project_id,
                resource_id=operation.operation_id,
                status="applied",
                details={"project_id": project_id, "relative_path": relative_path, "before_sha256": snapshot.content_sha256, "after_sha256": after_sha256},
            ))
        return operation

    def get_record(self, operation_id: str) -> ChangeRecord:
        entry = self._memory.get(operation_id)
        if entry is not None:
            return entry[0]
        if self._database is None:
            raise FileOperationError("Rollback operation does not exist")
        with closing(self._database.connect()) as connection:
            row = connection.execute("SELECT * FROM change_records WHERE operation_id = ?", (operation_id,)).fetchone()
        if row is None:
            raise FileOperationError("Rollback operation does not exist")
        return ChangeRecord(
            operation_id=row["operation_id"],
            project_id=row["project_id"],
            relative_path=row["relative_path"],
            backup_id=row["backup_id"],
            existed_before=bool(row["existed_before"]),
            before_sha256=row["before_sha256"],
            after_sha256=row["after_sha256"],
            status=row["status"],
        )

    def rollback(self, operation_id: str, target: Optional[Path] = None) -> ChangeRecord:
        entry = self._memory.get(operation_id)
        if entry is not None:
            record, snapshot = entry
        elif self._database is not None:
            with closing(self._database.connect()) as connection:
                row = connection.execute("SELECT * FROM change_records WHERE operation_id = ?", (operation_id,)).fetchone()
            if row is None:
                raise FileOperationError("Rollback operation does not exist")
            record = ChangeRecord(
                operation_id=row["operation_id"],
                project_id=row["project_id"],
                relative_path=row["relative_path"],
                backup_id=row["backup_id"],
                existed_before=bool(row["existed_before"]),
                before_sha256=row["before_sha256"],
                after_sha256=row["after_sha256"],
                status=row["status"],
            )
            snapshot = BackupSnapshot(
                backup_id=record.backup_id,
                project_id=record.project_id,
                relative_path=record.relative_path,
                existed=record.existed_before,
                content_sha256=record.before_sha256,
                size_bytes=row["size_bytes"] if "size_bytes" in row.keys() else 0,
                storage_path=self._backups._root / record.project_id / f"{record.backup_id}.bin",
            )
        else:
            raise FileOperationError("Rollback operation does not exist")
        if record.status != "applied":
            raise FileOperationError("Operation is not in an applied state")
        if target is None:
            raise FileOperationError("Rollback target must be resolved through the project sandbox")
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise FileOperationError("Rollback target is not a regular authorized file")
        current = target.read_bytes() if target.exists() else b""
        if hashlib.sha256(current).hexdigest() != record.after_sha256:
            raise FileOperationError("Current file changed since the operation; refusing conflicting rollback")
        try:
            original = self._backups.restore_bytes(snapshot)
        except (OSError, BackupIntegrityError) as exc:
            raise FileOperationError("Backup could not be verified") from exc
        if snapshot.existed:
            _atomic_replace(target, original)
        else:
            target.unlink(missing_ok=True)
        rolled_back = ChangeRecord(**{**record.__dict__, "status": "rolled_back"})
        self._memory[operation_id] = (rolled_back, snapshot)
        if self._database:
            with self._database.transaction() as connection:
                cursor = connection.execute("UPDATE change_records SET status = 'rolled_back' WHERE operation_id = ? AND status = 'applied'", (operation_id,))
                if cursor.rowcount != 1:
                    raise FileOperationError("Rollback state changed concurrently")
        if self._audit:
            self._audit.record(AuditEvent(
                actor_id="local-operator",
                event_type="file.rollback",
                project_id=record.project_id,
                resource_id=operation_id,
                status="rolled_back",
                details={"project_id": record.project_id, "relative_path": record.relative_path},
            ))
        return rolled_back


def _atomic_replace(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.rollback")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(target))
    finally:
        temporary.unlink(missing_ok=True)
