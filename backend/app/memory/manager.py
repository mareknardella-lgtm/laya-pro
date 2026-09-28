"""Project memory manager; all writes are explicit and secrets are rejected."""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from ..context.project_context import ProjectContextManager, ProjectContextError
from ..observability.audit import AuditEvent, AuditLog
from .models import MemoryEntry, MemoryScope
from .store import MemoryStore

_SECRET_KEY = re.compile(r"(password|secret|token|credential|api[_-]?key|private[_-]?key|access[_-]?key|authorization)", re.IGNORECASE)
_SECRET_VALUE = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/=-]{12,}|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b)",
    re.IGNORECASE,
)


class MemoryValidationError(ValueError):
    """Memory is invalid, too large, or appears to contain sensitive data."""


class MemoryManager:
    def __init__(
        self,
        projects: ProjectContextManager,
        store: MemoryStore,
        audit: AuditLog,
        max_value_chars: int = 8_000,
        temporary_ttl_seconds: int = 3_600,
    ) -> None:
        self._projects = projects
        self._store = store
        self._audit = audit
        self._max_value_chars = max_value_chars
        self._temporary_ttl = temporary_ttl_seconds
        self._temporary: dict[tuple[str, str], tuple[float, MemoryEntry]] = {}
        self._lock = threading.RLock()

    def put(self, project_id: str, key: str, value: Any, scope: MemoryScope = MemoryScope.TEMPORARY, actor_id: str = "local-operator") -> MemoryEntry:
        if project_id != "__global__":
            if project_id != "__global__": self._projects.resolve(project_id)
        self._validate_key(key)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded) > self._max_value_chars:
            raise MemoryValidationError("Memory value exceeds the configured size limit")
        self._reject_sensitive(value)
        now = datetime.now(timezone.utc)
        if scope == MemoryScope.PERSISTENT:
            entry = self._store.put(project_id, key, value)
        else:
            entry = MemoryEntry(
                project_id=project_id,
                key=key,
                value=value,
                scope=MemoryScope.TEMPORARY,
                created_at=now,
                updated_at=now,
            )
            with self._lock:
                self._expire_temporary()
                self._temporary[(project_id, key)] = (time.monotonic() + self._temporary_ttl, entry)
        self._audit.record(AuditEvent(
            actor_id=actor_id,
            event_type="memory.saved",
            project_id=project_id,
            resource_id=f"{project_id}:{key}",
            status="stored",
            details={"project_id": project_id, "key": key, "scope": scope.value, "value_bytes": len(encoded)},
        ))
        return entry

    def get(self, project_id: str, key: str, scope: MemoryScope = MemoryScope.PERSISTENT) -> Optional[MemoryEntry]:
        if project_id != "__global__": self._projects.resolve(project_id)
        self._validate_key(key)
        if scope == MemoryScope.PERSISTENT:
            return self._store.get(project_id, key)
        with self._lock:
            self._expire_temporary()
            item = self._temporary.get((project_id, key))
            return item[1] if item else None

    def list(self, project_id: str, scope: MemoryScope = MemoryScope.PERSISTENT, limit: int = 100) -> list[MemoryEntry]:
        if project_id != "__global__": self._projects.resolve(project_id)
        if scope == MemoryScope.PERSISTENT:
            return self._store.list(project_id, limit)
        with self._lock:
            self._expire_temporary()
            values = [entry for (owner, _key), (_expiry, entry) in self._temporary.items() if owner == project_id]
        return sorted(values, key=lambda entry: entry.key)[:max(1, min(limit, 500))]

    def delete(self, project_id: str, key: str, scope: MemoryScope = MemoryScope.PERSISTENT, actor_id: str = "local-operator") -> bool:
        if project_id != "__global__": self._projects.resolve(project_id)
        self._validate_key(key)
        if scope == MemoryScope.PERSISTENT:
            deleted = self._store.delete(project_id, key)
        else:
            with self._lock:
                deleted = self._temporary.pop((project_id, key), None) is not None
        if deleted:
            self._audit.record(AuditEvent(
                actor_id=actor_id,
                event_type="memory.deleted",
                project_id=project_id,
                resource_id=f"{project_id}:{key}",
                status="deleted",
                details={"project_id": project_id, "key": key, "scope": scope.value},
            ))
        return deleted

    @staticmethod
    def _validate_key(key: str) -> None:
        if not key or len(key) > 128 or not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
            raise MemoryValidationError("Memory keys must be bounded identifiers")
        if _SECRET_KEY.search(key):
            raise MemoryValidationError("Memory keys that indicate credentials or secrets are forbidden")

    @classmethod
    def _reject_sensitive(cls, value: Any, depth: int = 0) -> None:
        if depth > 8:
            raise MemoryValidationError("Memory nesting is too deep")
        if isinstance(value, dict):
            for key, child in value.items():
                if _SECRET_KEY.search(str(key)):
                    raise MemoryValidationError("Sensitive fields are not saved to project memory")
                cls._reject_sensitive(child, depth + 1)
        elif isinstance(value, (list, tuple)):
            for child in value:
                cls._reject_sensitive(child, depth + 1)
        elif isinstance(value, str) and (value.strip().lower() in {"secret", "password", "credential"} or _SECRET_VALUE.search(value)):
            raise MemoryValidationError("Memory value appears to contain a secret and was not saved")

    def _expire_temporary(self) -> None:
        now = time.monotonic()
        expired = [key for key, (deadline, _entry) in self._temporary.items() if deadline <= now]
        for key in expired:
            self._temporary.pop(key, None)
