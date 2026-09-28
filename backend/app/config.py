"""Central, fail-closed configuration for the local backend."""

from __future__ import annotations

import ipaddress
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read a deliberately small .env format without adding a runtime dependency."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _local_url(value: Optional[str], name: str) -> Optional[str]:
    if value is None or not value.strip():
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{name} must be an explicit http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not contain credentials, query strings, or fragments")
    host = parsed.hostname.lower()
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host in {"localhost", "127.0.0.1", "::1"}
    if not is_loopback:
        raise ValueError(f"{name} must target a loopback address; remote services are disabled")
    return value.rstrip("/")


def _nvidia_url(value: Optional[str], name: str) -> Optional[str]:
    if value is None or not value.strip():
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{name} must be an explicit http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not contain credentials, query strings, or fragments")
    host = parsed.hostname.lower()
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host in {"localhost", "127.0.0.1", "::1"}
    is_nvidia_host = host in {"integrate.api.nvidia.com", "api.nvidia.com"} or host.endswith(".nvidia.com")
    if not (is_loopback or is_nvidia_host):
        raise ValueError(f"{name} must target either a loopback address (for local NIM) or an official nvidia.com API host")
    return value.rstrip("/")


class Settings(BaseModel):
    """Validated settings; non-loopback binding and remote model endpoints are forbidden."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    app_mode: str = "local"
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    laya_decision_url: Optional[str] = None
    laya_api_key: Optional[str] = Field(default=None, repr=False, min_length=1, max_length=512)
    laya_local_path: Optional[Path] = None
    laya_python_path: Optional[Path] = None
    laya_wrapper_path: Optional[Path] = None
    laya_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    laya_max_input_chars: int = Field(default=12_000, ge=1, le=100_000)
    laya_max_output_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    system2_generate_url: Optional[str] = None
    system2_provider: str = Field(default="default", max_length=64)
    system2_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    system2_max_input_chars: int = Field(default=12_000, ge=1, le=100_000)
    system2_max_output_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)

    # JEV Hybrid Framework Config
    jev_api_url: Optional[str] = Field(default=None)
    jev_api_key: Optional[str] = Field(default=None)
    jev_timeout_seconds: float = Field(default=30.0, gt=0, le=120)

    nvidia_ai_enabled: bool = False
    nvidia_ai_provider: Optional[str] = Field(default=None, max_length=64)
    nvidia_ai_model: Optional[str] = Field(default=None, max_length=128)
    nvidia_ai_base_url: Optional[str] = None
    nvidia_ai_api_key: Optional[str] = Field(default=None, repr=False, min_length=1, max_length=512)
    nvidia_ai_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    max_iterations: int = Field(default=8, ge=1, le=64)
    project_roots: dict[str, Path] = Field(default_factory=dict)
    allowed_permissions: frozenset[str] = frozenset()
    approval_token: Optional[str] = Field(default=None, repr=False, min_length=16, max_length=256)
    database_path: Path = Path("backend/data/laya.sqlite3")
    approval_ttl_seconds: int = Field(default=900, ge=30, le=86_400)
    max_workflow_steps: int = Field(default=32, ge=1, le=256)
    workflow_timeout_seconds: float = Field(default=300.0, gt=0, le=3_600)
    max_file_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)
    max_memory_value_chars: int = Field(default=8_000, ge=1, le=100_000)
    watchdog_interval_seconds: float = Field(default=15.0, gt=0.0)
    watchdog_critical_interval_seconds: float = Field(default=2.0, gt=0.0)
    watchdog_failure_threshold: int = Field(default=2, ge=1)

    @field_validator("host")
    @classmethod
    def local_bind_only(cls, value: str) -> str:
        host = value.strip().lower()
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if not local:
            raise ValueError("Only a loopback host is allowed; Laya Pro must not bind publicly")
        return host

    @field_validator("app_mode")
    @classmethod
    def local_mode_only(cls, value: str) -> str:
        if value != "local":
            raise ValueError("Only local mode is currently supported")
        return value

    @field_validator("laya_decision_url")
    @classmethod
    def validate_laya_url(cls, value: Optional[str]) -> Optional[str]:
        return _local_url(value, "LAYA_DECISION_URL")

    @field_validator("system2_generate_url")
    @classmethod
    def validate_system2_url(cls, value: Optional[str]) -> Optional[str]:
        return _local_url(value, "SYSTEM2_GENERATE_URL")

    @field_validator("nvidia_ai_base_url")
    @classmethod
    def validate_nvidia_url(cls, value: Optional[str]) -> Optional[str]:
        return _nvidia_url(value, "NVIDIA_AI_BASE_URL")

    @field_validator("nvidia_ai_provider")
    @classmethod
    def validate_nvidia_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        normalized = value.strip().lower()
        if normalized not in {"nvidia", "nvidia-nim", "nvidia-ai", "nvidia-openai"}:
            raise ValueError(f"Unsupported NVIDIA AI provider {value!r}. Supported providers: nvidia, nvidia-nim")
        return normalized

    @field_validator("system2_provider")
    @classmethod
    def validate_system2_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"default", "local", "nvidia", "nvidia-nim", "nvidia-ai"}:
            raise ValueError(f"Unsupported System 2 provider {value!r}. Supported providers: default, local, nvidia, nvidia-nim")
        return normalized

    @property
    def active_system2_provider(self) -> str:
        if self.system2_provider in {"nvidia", "nvidia-nim", "nvidia-ai"} or (
            self.nvidia_ai_enabled and self.nvidia_ai_provider in {"nvidia", "nvidia-nim", "nvidia-ai"}
        ):
            return "nvidia"
        return "default"

    @field_validator("project_roots")
    @classmethod
    def validate_project_roots(cls, value: dict[str, Path]) -> dict[str, Path]:
        for project_id, root in value.items():
            if not project_id or len(project_id) > 64 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in project_id):
                raise ValueError("Project IDs may contain only letters, numbers, hyphens, and underscores")
            if not str(root).strip():
                raise ValueError(f"Project root for {project_id!r} cannot be empty")
        return value

    @model_validator(mode="after")
    def ensure_local_database(self) -> "Settings":
        if not self.database_path.is_absolute():
            resolved = (PROJECT_ROOT / self.database_path).resolve()
            if PROJECT_ROOT not in resolved.parents and resolved != PROJECT_ROOT:
                raise ValueError("Relative database_path must remain inside the repository")
        return self

    @classmethod
    def from_env(cls, env_file: Optional[Path] = None, environ: Optional[dict[str, str]] = None) -> "Settings":
        """Load LAYA_* settings. Existing process environment takes precedence over .env."""
        merged: dict[str, str] = _read_dotenv(env_file or PROJECT_ROOT / ".env")
        merged.update(os.environ if environ is None else environ)

        def maybe_int(key: str, default: int) -> int:
            return int(merged.get(key, default))

        def maybe_float(key: str, default: float) -> float:
            return float(merged.get(key, default))

        def maybe_bool(key: str, default: bool) -> bool:
            raw = merged.get(key)
            if raw is None:
                return default
            return raw.strip().lower() in {"1", "true", "yes", "on"}

        projects_raw = merged.get("LAYA_PROJECT_ROOTS", "{}")
        projects: Any = json.loads(projects_raw)
        if not isinstance(projects, dict):
            raise ValueError("LAYA_PROJECT_ROOTS must be a JSON object mapping project IDs to directories")

        permission_text = merged.get("LAYA_ALLOWED_PERMISSIONS", "")
        nvidia_enabled = maybe_bool("NVIDIA_AI_ENABLED", False)
        nvidia_provider = merged.get("NVIDIA_AI_PROVIDER") or None
        system2_prov = merged.get("SYSTEM2_PROVIDER") or ("nvidia" if (nvidia_enabled and nvidia_provider in {"nvidia", "nvidia-nim", "nvidia-ai"}) else "default")
        return cls(
            app_mode=merged.get("LAYA_MODE", "local"),
            host=merged.get("LAYA_HOST", "127.0.0.1"),
            port=maybe_int("LAYA_PORT", 8765),
            laya_decision_url=merged.get("LAYA_DECISION_URL") or None,
            laya_api_key=merged.get("LAYA_API_KEY") or None,
            laya_local_path=Path(merged["LAYA_LOCAL_PATH"]).expanduser() if merged.get("LAYA_LOCAL_PATH") else None,
            laya_python_path=Path(merged["LAYA_PYTHON_PATH"]).expanduser() if merged.get("LAYA_PYTHON_PATH") else None,
            laya_wrapper_path=Path(merged["LAYA_WRAPPER_PATH"]).expanduser() if merged.get("LAYA_WRAPPER_PATH") else None,
            laya_timeout_seconds=maybe_float("LAYA_TIMEOUT_SECONDS", 10.0),
            laya_max_input_chars=maybe_int("LAYA_MAX_INPUT_CHARS", 12_000),
            laya_max_output_bytes=maybe_int("LAYA_MAX_OUTPUT_BYTES", 65_536),
            system2_generate_url=merged.get("SYSTEM2_GENERATE_URL") or None,
            system2_provider=system2_prov,
            system2_timeout_seconds=maybe_float("SYSTEM2_TIMEOUT_SECONDS", 20.0),
            system2_max_input_chars=maybe_int("SYSTEM2_MAX_INPUT_CHARS", 12_000),
            system2_max_output_bytes=maybe_int("SYSTEM2_MAX_OUTPUT_BYTES", 65_536),
            jev_api_url=merged.get("JEV_API_URL") or None,
            jev_api_key=merged.get("JEV_API_KEY") or None,
            jev_timeout_seconds=maybe_float("JEV_TIMEOUT_SECONDS", 30.0),
            nvidia_ai_enabled=nvidia_enabled,
            nvidia_ai_provider=nvidia_provider,
            nvidia_ai_model=merged.get("NVIDIA_AI_MODEL") or None,
            nvidia_ai_base_url=merged.get("NVIDIA_AI_BASE_URL") or None,
            nvidia_ai_api_key=merged.get("NVIDIA_AI_API_KEY") or None,
            nvidia_ai_timeout_seconds=maybe_float("NVIDIA_AI_TIMEOUT_SECONDS", 30.0),
            max_iterations=maybe_int("LAYA_MAX_ITERATIONS", 8),
            project_roots={str(key): Path(value).expanduser() for key, value in projects.items()},
            allowed_permissions=frozenset(item.strip() for item in permission_text.split(",") if item.strip()),
            approval_token=merged.get("LAYA_APPROVAL_TOKEN") or None,
            database_path=Path(merged.get("LAYA_DATABASE_PATH", "backend/data/laya.sqlite3")).expanduser(),
            approval_ttl_seconds=maybe_int("LAYA_APPROVAL_TTL_SECONDS", 900),
            max_workflow_steps=maybe_int("LAYA_MAX_WORKFLOW_STEPS", 32),
            workflow_timeout_seconds=maybe_float("LAYA_WORKFLOW_TIMEOUT_SECONDS", 300.0),
            max_file_bytes=maybe_int("LAYA_MAX_FILE_BYTES", 1_048_576),
            max_memory_value_chars=maybe_int("LAYA_MAX_MEMORY_VALUE_CHARS", 8_000),
            watchdog_interval_seconds=maybe_float("LAYA_WATCHDOG_INTERVAL_SECONDS", 15.0),
            watchdog_critical_interval_seconds=maybe_float("LAYA_WATCHDOG_CRITICAL_INTERVAL_SECONDS", 2.0),
            watchdog_failure_threshold=maybe_int("LAYA_WATCHDOG_FAILURE_THRESHOLD", 2),
        )

    def resolved_database_path(self) -> Path:
        path = self.database_path.expanduser()
        return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    def resolved_project_roots(self) -> dict[str, Path]:
        return {
            project_id: (root if root.is_absolute() else PROJECT_ROOT / root).expanduser().resolve()
            for project_id, root in self.project_roots.items()
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
