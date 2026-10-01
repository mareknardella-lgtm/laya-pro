"""Application settings, loaded from the environment or a .env file.Note on naming: there is deliberately no global ``env_prefix``. A prefix would
    force every variable to share one prefix, which conflicts with the provider
    names already in use (NEMOTRON_*, LAYA_COREML_*). Each field therefore declares
    the exact variable names it answers to, so both the documented name and the
    shorter alias work.

    Precedence, as observed with pydantic-settings 2.x and worth remembering:
    an exported environment variable beats .env, but .env beats a keyword passed
    to ``Settings(...)``. Code that builds Settings programmatically should pass
    ``_env_file=None`` to opt out of the file entirely.
    """

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("LAYA_HOST"))
    port: int = Field(default=8000, validation_alias=AliasChoices("LAYA_PORT"))
    log_level: str = Field(default="INFO", validation_alias=AliasChoices("LAYA_LOG_LEVEL"))
    db_path: Path = Field(
        default=Path("./data/laya.db"), validation_alias=AliasChoices("LAYA_DB_PATH")
    )

    # System 1 - Nemotron 3.5 Lightning: low latency generation.
    nemotron_api_key: str = Field(
        default="", validation_alias=AliasChoices("NEMOTRON_API_KEY", "LAYA_NEMOTRON_API_KEY")
    )
    nemotron_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        validation_alias=AliasChoices("NEMOTRON_BASE_URL", "LAYA_NEMOTRON_BASE_URL"),
    )
    nemotron_model: str = Field(
        default="nvidia/nemotron-3.5-lightning-30b-a3b",
        validation_alias=AliasChoices("NEMOTRON_MODEL", "LAYA_NEMOTRON_MODEL"),
    )
    nemotron_timeout_seconds: float = Field(
        default=120.0, validation_alias=AliasChoices("NEMOTRON_TIMEOUT_SECONDS")
    )
    nemotron_max_output_tokens: int = Field(
        default=800, validation_alias=AliasChoices("NEMOTRON_MAX_OUTPUT_TOKENS")
    )
    nemotron_enable_thinking: bool = Field(
        default=False, validation_alias=AliasChoices("NEMOTRON_ENABLE_THINKING")
    )
    nemotron_temperature: float = Field(
        default=0.3, validation_alias=AliasChoices("NEMOTRON_TEMPERATURE")
    )

    # System 2 - laya-coreml: deep reasoning over the local Core ML runtime.
    laya_coreml_url: str = Field(
        default="http://127.0.0.1:8081",
        validation_alias=AliasChoices("LAYA_COREML_URL", "COREML_URL"),
    )
    laya_coreml_api_key: str = Field(
        default="", validation_alias=AliasChoices("LAYA_COREML_API_KEY", "COREML_API_KEY")
    )
    laya_coreml_timeout_seconds: float = Field(
        default=120.0, validation_alias=AliasChoices("LAYA_COREML_TIMEOUT_SECONDS")
    )
    laya_max_input_chars: int = Field(
        default=12_000, validation_alias=AliasChoices("LAYA_COREML_MAX_INPUT_CHARS")
    )
    laya_max_refinements: int = Field(
        default=2, ge=0, le=5, validation_alias=AliasChoices("LAYA_MAX_REFINEMENTS")
    )
    laya_engine_label: str = Field(
        default="laya-coreml", validation_alias=AliasChoices("LAYA_ENGINE_LABEL")
    )

    @property
    def nemotron_configured(self) -> bool:
        return bool(self.nemotron_api_key and self.nemotron_base_url)

    @property
    def laya_configured(self) -> bool:
        return bool(self.laya_coreml_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()