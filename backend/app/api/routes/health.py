"""Health routes for backend process and integrated local services."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from ...models import HealthResponse

router = APIRouter(tags=["health"])
logger = logging.getLogger("laya.health")


class ServiceHealthItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: str  # "online", "offline", "error"
    endpoint: Optional[str] = None
    latency_ms: Optional[float] = None
    checked_at: str
    message: str


class ServicesHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_status: str  # "all_operational", "partial", "offline"
    backend: ServiceHealthItem
    system1: ServiceHealthItem


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    try:
        checked_at = datetime.now(timezone.utc).isoformat()
        with request.app.state.database.transaction() as connection:
            connection.execute(
                "INSERT INTO system_health_checks (checked_at, api_status) VALUES (?, 'healthy')",
                (checked_at,),
            )
            connection.execute(
                "DELETE FROM system_health_checks WHERE checked_at NOT IN (SELECT checked_at FROM system_health_checks ORDER BY checked_at DESC LIMIT 1000)"
            )
    except Exception:
        logger.warning("Health check could not persist its observation")
    return HealthResponse()


@router.get("/api/v1/health/services", response_model=ServicesHealthResponse)
async def check_services_health(request: Request) -> ServicesHealthResponse:
    """Non-blocking health probe for FastAPI backend and Laya System 1."""
    now_iso = datetime.now(timezone.utc).isoformat()
    backend_start = time.perf_counter()
    backend_item = ServiceHealthItem(
        name="Backend FastAPI",
        status="online",
        endpoint=f"http://{request.app.state.settings.host}:{request.app.state.settings.port}",
        latency_ms=round((time.perf_counter() - backend_start) * 1000, 2),
        checked_at=now_iso,
        message="Backend operativo e reattivo",
    )

    # Verifica System 1 (daemon HTTP laya.serve o wrapper locale)
    settings = request.app.state.settings
    s1_url = settings.laya_decision_url or "http://127.0.0.1:8000/v1/systemone"
    s1_health_url = s1_url.replace("/v1/systemone", "/health")

    s1_status = "offline"
    s1_latency = None
    s1_message = "System 1 non raggiungibile"

    s1_start = time.perf_counter()
    try:
        # 1. Prova endpoint HTTP /health del demone laya.serve
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(s1_health_url)
            s1_latency = round((time.perf_counter() - s1_start) * 1000, 2)
            if resp.status_code == 200:
                s1_status = "online"
                s1_message = "Laya System 1 daemon operativo"
            else:
                s1_status = "error"
                s1_message = f"Risposta HTTP {resp.status_code} dal demone System 1"
    except httpx.ConnectError:
        # Demone HTTP non risponde. Se è configurato il local_bridge (laya_torch.py), verifichiamo la configurazione
        adapter = getattr(request.app.state, "laya_adapter", None)
        bridge = getattr(adapter, "_bridge", None) if adapter else None
        if bridge and bridge.is_configured:
            s1_latency = round((time.perf_counter() - s1_start) * 1000, 2)
            s1_status = "online"
            s1_message = "Laya System 1 operativo tramite wrapper locale (laya_torch.py)"
        else:
            s1_status = "offline"
            s1_message = "System 1 non raggiungibile (connessione rifiutata)"
    except httpx.TimeoutException:
        s1_status = "error"
        s1_message = "Timeout durante la verifica di System 1"
    except Exception as exc:
        s1_status = "error"
        s1_message = f"Errore verifica System 1: {str(exc)}"

    system1_item = ServiceHealthItem(
        name="Laya System 1",
        status=s1_status,
        endpoint=s1_url,
        latency_ms=s1_latency,
        checked_at=now_iso,
        message=s1_message,
    )

    if backend_item.status == "online" and system1_item.status == "online":
        overall = "all_operational"
    elif backend_item.status == "online" or system1_item.status == "online":
        overall = "partial"
    else:
        overall = "offline"

    return ServicesHealthResponse(
        overall_status=overall,
        backend=backend_item,
        system1=system1_item,
    )
