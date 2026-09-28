"""Project context boundary; project roots are never inferred from request text."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import Settings


class ProjectContextError(ValueError):
    """The requested project is not explicitly configured and authorized."""


@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    root: Path


class ProjectContextManager:
    def __init__(self, settings: Settings) -> None:
        self._roots = settings.resolved_project_roots()

    @property
    def project_ids(self) -> frozenset[str]:
        return frozenset(self._roots)

    def resolve(self, project_id: Optional[str]) -> Optional[ProjectContext]:
        if project_id is None:
            return None
        root = self._roots.get(project_id)
        if root is None:
            raise ProjectContextError("Project is not configured as an authorized project")
        return ProjectContext(project_id=project_id, root=root)
