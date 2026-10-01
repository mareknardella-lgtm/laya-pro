"""Entry point that works no matter where you launch it from.

Running ``python backend/app/main.py`` or ``uvicorn backend.app.main:app``
from inside ``backend/`` fails, because the package path ``backend.app`` only
resolves from the project root. This launcher pins the root on ``sys.path`` and
pins the working directory too, so ``.env`` and ``./data`` always resolve the
same way.

    python run.py              # start (only LOW works)
    python run.py --with-stub  # start, and bring up the reasoning stand-in too
    python run.py --reload     # start with auto-reload
    python run.py --port 8080  # override the port
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Optional

ROOT = os.path.dirname(os.path.abspath(__file__))


def bootstrap() -> None:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    # .env and the SQLite default path are relative to the project root.
    os.chdir(ROOT)


def say(message: str) -> None:
    """Unbuffered: the banner must appear before uvicorn takes over the console."""
    print(message, flush=True)


def start_stub(settings) -> Optional[subprocess.Popen]:
    """Launch the reasoning stand-in as its own process.

    It stays a separate process on purpose: the runtime boundary is the whole
    point of the contract. This only saves the user a second terminal window.
    """
    if _reasoning_runtime_alive(settings.laya_coreml_url):
        say(f"laya-coreml gia' in ascolto su {settings.laya_coreml_url}")
        return None

    stub = os.path.join(ROOT, "tools", "laya_coreml_stub.py")
    say(f"avvio laya-coreml stand-in: {stub}")
    return subprocess.Popen([sys.executable, stub], cwd=ROOT)


def _reasoning_runtime_alive(url: str) -> bool:
    """Cheap reachability probe so startup says what will not work."""
    import urllib.error
    import urllib.request
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    probe = f"{parts.scheme}://{parts.netloc}/health"
    try:
        with urllib.request.urlopen(probe, timeout=1.5):
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Avvia Laya Pro")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--with-stub",
        action="store_true",
        help="avvia anche lo stand-in di laya-coreml (consente MEDIUM e HARD)",
    )
    args = parser.parse_args()

    bootstrap()

    import uvicorn

    from backend.app.config import get_settings

    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    say(f"Laya Pro  ->  http://{host}:{port}   (db: {settings.db_path})")

    # Start the reasoning stand-in before probing it, or the warning would
    # fire for a runtime we are about to bring up ourselves.
    stub_process = start_stub(settings) if args.with_stub else None
    if stub_process is not None:
        # Give it a moment so the reachability probe below is meaningful.
        import time

        for _ in range(40):
            if _reasoning_runtime_alive(settings.laya_coreml_url):
                break
            time.sleep(0.25)

    if not settings.nemotron_configured:
        say("ATTENZIONE: NEMOTRON_API_KEY non trovata: nessuna modalita' puo' rispondere.")
    if not _reasoning_runtime_alive(settings.laya_coreml_url):
        say(
            "ATTENZIONE: laya-coreml non risponde su "
            f"{settings.laya_coreml_url}: solo LOW funziona.\n"
            "              per MEDIUM e HARD usa:  python run.py --with-stub"
        )

    try:
        if args.reload:
            uvicorn.run("backend.app.main:app", host=host, port=port, reload=True)
        else:
            from backend.app.main import app

            uvicorn.run(app, host=host, port=port)
    finally:
        if stub_process is not None and stub_process.poll() is None:
            say("arresto laya-coreml stand-in")
            stub_process.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())