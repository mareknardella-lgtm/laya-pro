"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import chat, memory, sessions, status
from .config import get_settings
from .container import Container
from .static import STATIC_DIR
from .systems.laya.exceptions import LayaContractError, LayaRefusal, LayaUnavailable
from .systems.nemotron.exceptions import (
    NemotronContractError,
    NemotronUnavailable,
)

logger = logging.getLogger("laya.app")


def create_app(container: Container = None) -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = container or Container(settings)
        await app.state.container.startup()
        # Log what the container actually uses, not the global settings: an
        # injected container may point elsewhere.
        logger.info("Laya backend ready (db=%s)", app.state.container.settings.db_path)
        yield
        if container is None:
            await app.state.container.shutdown()

    app = FastAPI(
        title="Laya Hybrid Backend",
        version="1.0.0",
        description=(
            "Hybrid System 1/2 orchestrator: laya-coreml provides deep reasoning, "
            "Nemotron 3.5 Lightning provides low-latency generation."
        ),
        lifespan=lifespan,
    )

    app.include_router(status.router)
    app.include_router(chat.router)
    app.include_router(memory.router)
    app.include_router(sessions.router)
    _register_error_handlers(app)

    # Mounted last: API routes win over the SPA for every shared path.
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "favicon.svg"), media_type="image/svg+xml")

    return app


def _register_error_handlers(app: FastAPI) -> None:
    """Map runtime failures onto honest HTTP statuses."""

    @app.exception_handler(LayaUnavailable)
    async def _laya_unavailable(request: Request, exc: LayaUnavailable) -> JSONResponse:
        logger.error("reasoning tier unavailable: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc), "engine": "laya-coreml", "recoverable": True},
        )

    @app.exception_handler(NemotronUnavailable)
    async def _nemotron_unavailable(request: Request, exc: NemotronUnavailable) -> JSONResponse:
        logger.error("fast tier unavailable: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc), "engine": "nemotron", "recoverable": True},
        )

    @app.exception_handler(LayaContractError)
    async def _laya_contract(request: Request, exc: LayaContractError) -> JSONResponse:
        logger.error("reasoning contract violated: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"detail": str(exc), "engine": "laya-coreml"},
        )

    @app.exception_handler(LayaRefusal)
    async def _laya_refused(request: Request, exc: LayaRefusal) -> JSONResponse:
        """A refusal is a legitimate answer from the runtime, not a crash.

        It means the reasoning engine could not plan this problem. Inventing a
        plan here is exactly what the orchestrator must never do.
        """
        logger.warning("reasoning refused: %s (%s)", exc, exc.reason)
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    "laya-coreml ha rifiutato di pianificare questo problema"
                    + (f": {exc.reason}" if exc.reason else "")
                ),
                "engine": "laya-coreml",
                "reason": exc.reason,
                "hint": "Riformula il problema, oppure usa LOW.",
            },
        )

    @app.exception_handler(NemotronContractError)
    async def _nemotron_contract(
        request: Request, exc: NemotronContractError
    ) -> JSONResponse:
        logger.error("generation contract violated: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"detail": str(exc), "engine": "nemotron"},
        )


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "backend.app.main:app", host=settings.host, port=settings.port, reload=False
    )