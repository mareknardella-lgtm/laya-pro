"""Runtime status for both engines."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request

router = APIRouter(tags=["status"])


@router.get("/status")
async def status(request: Request) -> Dict[str, Any]:
    container = request.app.state.container
    settings = container.settings
    return {
        "engines": {
            "nemotron": {
                "configured": container.nemotron.runtime_configured,
                "model": settings.nemotron_model,
                "role": "fast generation",
                **container.nemotron.observation.snapshot(),
            },
            "laya-coreml": {
                "configured": container.laya.runtime_configured,
                "model": settings.laya_engine_label,
                "role": "deep reasoning",
                **container.laya.observation.snapshot(),
            },
        },
        "tiers": {
            "LOW": "nemotron only",
            "MEDIUM": "laya-coreml plan -> nemotron answer",
            "HARD": "laya-coreml plan -> nemotron answer -> laya-coreml critique",
        },
        "database": str(settings.db_path),
    }