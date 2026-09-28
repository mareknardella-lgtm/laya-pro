"""Autonomous watchdog for monitoring System 1 health and protecting workflows."""

import asyncio
import logging
from typing import Optional

import httpx
from datetime import datetime, timezone

from ..config import Settings
from ..workflows.engine import WorkflowEngine
from ..workflows.models import WorkflowStatus
from ..workflows.state import WorkflowStateStore
from .audit import AuditEvent, AuditLog

logger = logging.getLogger("laya.watchdog")

class SystemWatchdog:
    """Monitors System 1 health and mitigates failure during critical workflows."""
    
    def __init__(
        self,
        settings: Settings,
        workflow_engine: WorkflowEngine,
        workflow_store: WorkflowStateStore,
        audit_log: AuditLog,
        *args, **kwargs
    ) -> None:
        self.settings = settings
        self.engine = workflow_engine
        self.store = workflow_store
        self.audit = audit_log
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._system_down = False
        self._consecutive_failures = 0
        self._lock: Optional[asyncio.Lock] = None

    def start(self) -> None:
        """Start the watchdog in a background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("System watchdog started")

    async def stop(self) -> None:
        """Stop the watchdog background task."""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("System watchdog stopped")

    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "system_down": self._system_down,
            "consecutive_failures": self._consecutive_failures
        }

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Watchdog iteration failed unexpectedly: {e}")
            
            is_critical = self._has_critical_workflows()
            interval = (
                self.settings.watchdog_critical_interval_seconds
                if is_critical
                else self.settings.watchdog_interval_seconds
            )
            
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    def _has_critical_workflows(self) -> bool:
        """Check if any critical workflow is currently active."""
        workflows = self.store.list(limit=1000)
        for wf in workflows:
            if wf.status in {WorkflowStatus.RUNNING, WorkflowStatus.AWAITING_APPROVAL}:
                for step in wf.definition.steps:
                    if "replace" in step.tool_id or "rollback" in step.tool_id:
                        return True
        return False

    async def _check(self) -> None:
        """Perform a single health check iteration."""
        s1_url = self.settings.laya_decision_url or "http://127.0.0.1:8000/v1/systemone"
        health_url = s1_url.replace("/v1/systemone", "/health")
        
        is_healthy = False
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(health_url)
                if resp.status_code == 200:
                    is_healthy = True
        except Exception:
            pass

        if self._lock is None:
            self._lock = asyncio.Lock()

        async with self._lock:
            if is_healthy:
                if self._system_down:
                    logger.info("System 1 health check recovered")
                    self._system_down = False
                    self._consecutive_failures = 0
                    self.audit.record(AuditEvent(
                        actor_id="system:watchdog",
                        event_type="system1.recovered",
                        status="healthy",
                        details={"message": "System 1 health recovered. Workflows will not auto-resume."}
                    ))
                else:
                    self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
                if not self._system_down and self._consecutive_failures >= self.settings.watchdog_failure_threshold:
                    logger.warning("System 1 is down. Watchdog threshold reached.")
                    self._system_down = True
                    await self._handle_down_event()

    async def _handle_down_event(self) -> None:
        """Pause critical workflows and record an incident when System 1 is down."""
        workflows = self.store.list(limit=1000)
        affected_workflows = []
        
        for wf in workflows:
            if wf.status in {WorkflowStatus.RUNNING, WorkflowStatus.AWAITING_APPROVAL}:
                is_critical = False
                for step in wf.definition.steps:
                    if "replace" in step.tool_id or "rollback" in step.tool_id:
                        is_critical = True
                        break
                
                if is_critical:
                    try:
                        await self.engine.pause(wf.workflow_id)
                        affected_workflows.append(wf.workflow_id)
                        self.audit.record(AuditEvent(
                            actor_id="system:watchdog",
                            event_type="workflow.watchdog_paused",
                            project_id=wf.project_id,
                            workflow_id=wf.workflow_id,
                            resource_id=wf.workflow_id,
                            status="paused",
                            details={"reason": "System 1 went offline during critical workflow operation"}
                        ))
                    except Exception as e:
                        logger.error(f"Watchdog failed to pause workflow {wf.workflow_id}: {e}")

        self.audit.record(AuditEvent(
            actor_id="system:watchdog",
            event_type="system1.down",
            status="down",
            details={
                "message": f"System 1 health checks failed {self._consecutive_failures} times",
                "affected_workflows": affected_workflows
            }
        ))
