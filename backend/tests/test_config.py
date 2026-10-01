"""Regression tests for settings loading.

These exist because a global ``env_prefix`` once made every documented variable
name wrong: NEMOTRON_API_KEY sat unread in .env while the app looked for
LAYA_NEMOTRON_API_KEY.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.config import Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Real .env and exported variables must not leak into these assertions."""

    for name in list(os.environ):
        if name.startswith(("LAYA_", "NEMOTRON_", "COREML_")):
            monkeypatch.delenv(name, raising=False)


def write_env(path: Path, body: str) -> Path:
    env = path / ".env"
    env.write_text(body, encoding="utf-8")
    return env


def test_documented_nemotron_variables_are_read(tmp_path):
    env = write_env(
        tmp_path,
        "NEMOTRON_API_KEY=nvapi-test-key\n"
        "NEMOTRON_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b\n"
        "NEMOTRON_TEMPERATURE=0.7\n",
    )

    settings = Settings(_env_file=env)

    assert settings.nemotron_api_key == "nvapi-test-key"
    assert settings.nemotron_temperature == pytest_approx(0.7)
    assert settings.nemotron_configured is True


def test_documented_laya_variables_are_read(tmp_path):
    env = write_env(
        tmp_path,
        "LAYA_COREML_URL=http://127.0.0.1:9999\n"
        "LAYA_MAX_REFINEMENTS=4\n"
        "LAYA_DB_PATH=./custom.db\n",
    )

    settings = Settings(_env_file=env)

    assert settings.laya_coreml_url == "http://127.0.0.1:9999"
    assert settings.laya_max_refinements == 4
    assert settings.db_path == Path("./custom.db")


def test_short_coreml_alias_is_accepted(tmp_path):
    env = write_env(tmp_path, "COREML_URL=http://alias.local\n")

    assert Settings(_env_file=env).laya_coreml_url == "http://alias.local"


def test_a_prefixed_nemotron_key_still_works(tmp_path):
    """The prefix variant is accepted too, so an existing setup keeps running."""

    env = write_env(tmp_path, "LAYA_NEMOTRON_API_KEY=nvapi-legacy\n")

    assert Settings(_env_file=env).nemotron_api_key == "nvapi-legacy"


def test_missing_key_leaves_the_tier_unconfigured(tmp_path):
    env = write_env(tmp_path, "NEMOTRON_MODEL=nvidia/something\n")

    settings = Settings(_env_file=env)

    assert settings.nemotron_api_key == ""
    assert settings.nemotron_configured is False


def test_field_names_still_work_for_direct_construction():
    """Tests build Settings(nemotron_api_key=...); aliases must not break that."""

    settings = Settings(_env_file=None, nemotron_api_key="direct", laya_max_refinements=1)

    assert settings.nemotron_api_key == "direct"
    assert settings.laya_max_refinements == 1


def test_environment_variable_overrides_dotenv(tmp_path, monkeypatch):
    """Exporting a variable must win over .env: that is how you try another key
    without editing the file."""

    env = write_env(tmp_path, "NEMOTRON_API_KEY=from-dotenv\n")
    monkeypatch.setenv("NEMOTRON_API_KEY", "from-environment")

    settings = Settings(_env_file=env)

    assert settings.nemotron_api_key == "from-environment"


def test_env_file_defaults_are_sane():
    settings = Settings(_env_file=None)

    assert settings.nemotron_model == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert settings.laya_coreml_url == "http://127.0.0.1:8081"
    assert settings.db_path == Path("./data/laya.db")


def pytest_approx(value):
    return pytest.approx(value)