from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backend.app.changes.backup import BackupManager
from backend.app.changes.patch_engine import FileOperationError, PatchEngine
from backend.app.changes.rollback import RollbackManager
from backend.app.config import Settings
from backend.app.sandbox.manager import SandboxManager, SandboxViolation


def test_sandbox_rejects_parent_traversal_and_absolute_paths(tmp_path: Path) -> None:
    manager = SandboxManager({"demo": tmp_path})
    for value in ("../outside.txt", "nested/../../outside.txt", str(tmp_path / "inside.txt")):
        with pytest.raises(SandboxViolation):
            manager.resolve("demo", value)


def test_sandbox_rejects_symlinks_that_escape_project(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable in this test environment")
    manager = SandboxManager({"demo": root})
    with pytest.raises(SandboxViolation):
        manager.resolve("demo", "linked/secret.txt")


def test_patch_engine_writes_atomically_with_digest_and_backup(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "notes.txt"
    target.write_bytes(b"before\n")
    database_path = tmp_path / "state.sqlite3"
    settings = Settings(project_roots={"demo": project}, database_path=database_path)
    sandbox = SandboxManager(settings.resolved_project_roots())
    backup = BackupManager(tmp_path / "backups")
    rollback = RollbackManager(backup)
    engine = PatchEngine(sandbox, backup, rollback, max_file_bytes=1024)

    operation = engine.replace("demo", "notes.txt", "after\n", expected_sha256=hashlib.sha256(b"before\n").hexdigest())
    assert target.read_bytes() == b"after\n"
    assert operation.before_sha256 == hashlib.sha256(b"before\n").hexdigest()
    assert operation.after_sha256 == hashlib.sha256(b"after\n").hexdigest()
    assert operation.backup_id
    restored = rollback.rollback(operation.operation_id, target)
    assert restored.status == "rolled_back"
    assert target.read_bytes() == b"before\n"


def test_patch_engine_detects_conflict_before_change(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "data.txt"
    target.write_text("current", encoding="utf-8")
    sandbox = SandboxManager({"demo": root})
    backup = BackupManager(tmp_path / "backups")
    engine = PatchEngine(sandbox, backup, RollbackManager(backup), max_file_bytes=1024)
    with pytest.raises(FileOperationError):
        engine.replace("demo", "data.txt", "new", expected_sha256="0" * 64)
    assert target.read_text(encoding="utf-8") == "current"


def test_rollback_detects_post_operation_conflict(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "data.txt"
    target.write_text("one", encoding="utf-8")
    backup = BackupManager(tmp_path / "backups")
    rollback = RollbackManager(backup)
    operation = backup.snapshot(target)
    target.write_text("two", encoding="utf-8")
    record = rollback.record_operation("demo", "data.txt", operation, hashlib.sha256(b"two").hexdigest())
    target.write_text("third-party", encoding="utf-8")
    with pytest.raises(FileOperationError):
        rollback.rollback(record.operation_id)
    assert target.read_text(encoding="utf-8") == "third-party"
