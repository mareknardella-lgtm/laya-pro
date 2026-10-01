"""Tests for the startup path.

Users launch this from all sorts of directories. ``backend.app`` only imports
from the project root, so the launcher has to make that true by itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_launcher_is_syntactically_valid():
    """A merged line once shipped a AttributeError only visible at startup."""

    import ast

    ast.parse((ROOT / "run.py").read_text(encoding="utf-8"))


def test_launcher_actually_starts_and_prints_its_banner(tmp_path):
    """--help never reaches the startup code, which is where the bugs lived."""

    import socket
    import time

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    process = subprocess.Popen(
        [sys.executable, str(ROOT / "run.py"), "--port", str(port)],
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        banner = ""
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            banner += line
            if "Laya Pro" in line:
                break
    finally:
        process.terminate()
        process.wait(timeout=15)

    assert "Laya Pro" in banner, banner
    assert "AttributeError" not in banner


def test_launcher_warns_when_the_reasoning_runtime_is_missing():
    """The warning must name the flag that fixes it."""

    source = (ROOT / "run.py").read_text(encoding="utf-8")
    assert "--with-stub" in source
    assert "solo LOW funziona" in source


def test_backend_app_imports_from_the_project_root():
    result = subprocess.run(
        [sys.executable, "-c", "import backend.app.main; print('ok')"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_direct_script_execution_fails_by_design():
    """Documents why run.py exists: the naive command cannot work."""

    result = subprocess.run(
        [sys.executable, str(ROOT / "backend" / "app" / "main.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode != 0
    assert "relative import" in result.stderr or "backend" in result.stderr


def test_launcher_works_from_the_project_root():
    result = subprocess.run(
        [sys.executable, "run.py", "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert "--port" in result.stdout


def test_launcher_works_from_a_subdirectory():
    """The regression that motivated run.py: launching from inside backend/."""

    result = subprocess.run(
        [sys.executable, str(ROOT / "run.py"), "--help"],
        cwd=str(ROOT / "backend"),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert "--reload" in result.stdout


def test_launcher_pins_the_root_and_cwd():
    source = (ROOT / "run.py").read_text(encoding="utf-8")

    assert "sys.path.insert(0, ROOT)" in source
    assert "os.chdir(ROOT)" in source, ".env and ./data must resolve from the root"


def test_packaging_metadata_exists():
    pyproject = ROOT / "pyproject.toml"
    assert pyproject.is_file()

    content = pyproject.read_text(encoding="utf-8")
    assert 'include = ["backend*"]' in content
    assert "static/*" in content, "the web UI must ship with the package"


def test_windows_launcher_targets_run_py():
    bat = ROOT / "start.bat"
    assert bat.is_file()
    assert "run.py" in bat.read_text(encoding="utf-8")
    assert "%~dp0" in bat.read_text(encoding="utf-8"), "must cd to its own directory"


def test_requirements_and_pyproject_agree():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()

    for package in ("fastapi", "uvicorn", "pydantic", "httpx"):
        assert package in requirements
        assert package in pyproject