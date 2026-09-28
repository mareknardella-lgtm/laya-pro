"""FastAPI application entry point."""

from __future__ import annotations

import logging
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_ROOT

from .context.project_context import ProjectContextError
from .core.action_router import ActionRouter
from .core.decision_engine import DecisionEngine, DecisionLimitError
from .core.orchestrator import Orchestrator
from .systems.laya.adapter import LayaAdapter
from .observability.runtime import RuntimeObservation
from .systems.laya.exceptions import LayaAdapterError
from .context.project_context import ProjectContextManager
from .api.routes.decisions import router as decisions_router
from .api.routes.status import router as status_router
from .api.routes.tools import router as tools_router
from .api.routes.approvals import router as approvals_router
from .api.routes.audit import router as audit_router
from .api.routes.files import router as files_router
from .api.routes.rollback import router as rollback_router
from .api.routes.workflows import router as workflows_router
from .api.routes.memory import router as memory_router
from .api.routes.system2 import router as system2_router
from .api.routes.activity import router as activity_router
from .api.routes.diagnostics import router as diagnostics_router
from .api.routes.chat import router as chat_router
from .systems.system2.adapter import System2Adapter
from .tools.registry import ToolRegistry
from .tools.executor import ToolExecutor
from .storage.sqlite import SQLiteDatabase
from .observability.audit import AuditLog
from .observability.executions import ExecutionHistoryStore
from .observability.activity import ActivityService
from .security.approval_gate import ApprovalGate
from .security.policy_engine import ApprovalRequiredError, PolicyDeniedError, PolicyEngine
from .security.approval_gate import ApprovalError
from .security.session import OperatorSessionManager
from .sandbox.manager import SandboxManager
from .changes.backup import BackupManager
from .changes.patch_engine import PatchEngine
from .changes.rollback import RollbackManager
from .tools.builtins.filesystem import file_action_reference, register_filesystem_tools
from .workflows.state import WorkflowStateStore
from .workflows.engine import WorkflowEngine
from .memory.store import MemoryStore
from .memory.manager import MemoryManager

from .api.routes.health import router as health_router
from .config import Settings, get_settings
from .models import ErrorResponse
from .observability.logging import configure_logging

logger = logging.getLogger("laya.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    watchdog = getattr(app.state, "watchdog", None)
    if watchdog is not None:
        watchdog.start()
    yield
    if watchdog is not None:
        await watchdog.stop()
    workflow_engine = getattr(app.state, "workflow_engine", None)
    if workflow_engine is not None:
        workflow_engine.cleanup_locks()


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    config = settings or get_settings()
    configure_logging()
    application = FastAPI(
        title="Laya Pro",
        description="Local-first hybrid AI backend. Model decisions are proposals, never authorization.",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    application.state.settings = config

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
        return response
    system1_observation = RuntimeObservation()
    system2_observation = RuntimeObservation()
    application.state.system2_adapter = System2Adapter(config, observation=system2_observation)
    laya_adapter = LayaAdapter(config, observation=system1_observation)
    application.state.laya_adapter = laya_adapter
    decision_engine = DecisionEngine(config, laya_adapter)

    database = SQLiteDatabase(config.resolved_database_path())
    audit_log = AuditLog(database)
    approval_gate = ApprovalGate(database, audit_log, config.approval_ttl_seconds)
    policy_engine = PolicyEngine(config, approval_gate)
    project_contexts = ProjectContextManager(config)
    sandbox = SandboxManager(config.resolved_project_roots(), config.max_file_bytes)
    backup_manager = BackupManager(config.resolved_database_path().parent / "backups", config.max_file_bytes)
    rollback_manager = RollbackManager(backup_manager, database, audit_log)
    patch_engine = PatchEngine(sandbox, backup_manager, rollback_manager, config.max_file_bytes)
    tool_registry = ToolRegistry()
    register_filesystem_tools(tool_registry, sandbox, patch_engine, rollback_manager)

    async def authorize_tool(definition, parameters, context, fingerprint, approval_id=None, action_ref=None):
        return policy_engine.authorize(definition, parameters, context, fingerprint, approval_id, action_ref)

    execution_history = ExecutionHistoryStore(database)
    tool_executor = ToolExecutor(
        tool_registry,
        authorize_tool,
        action_reference=file_action_reference,
        history=execution_history,
    )
    workflow_store = WorkflowStateStore(database, audit_log)
    workflow_engine = WorkflowEngine(
        config, workflow_store, tool_registry,
        project_contexts, policy_engine, approval_gate,
        tool_executor,
    )
    memory_store = MemoryStore(database)
    memory_manager = MemoryManager(
        project_contexts, memory_store, audit_log,
        config.max_memory_value_chars,
    )

    router = ActionRouter(project_contexts, decision_engine, memory_manager=memory_manager)
    orchestrator = Orchestrator(
        router,
        policy_engine=policy_engine,
        approval_gate=approval_gate,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        memory_manager=memory_manager,
        workflow_engine=workflow_engine,
    )

    session_manager = OperatorSessionManager()
    application.state.session_manager = session_manager
    application.state.database = database
    application.state.audit_log = audit_log
    application.state.approval_gate = approval_gate
    application.state.policy_engine = policy_engine
    application.state.project_contexts = project_contexts
    application.state.sandbox = sandbox
    application.state.backup_manager = backup_manager
    application.state.rollback_manager = rollback_manager
    application.state.patch_engine = patch_engine
    application.state.tool_registry = tool_registry
    application.state.execution_history = execution_history
    application.state.tool_executor = tool_executor
    application.state.workflow_store = workflow_store
    application.state.workflow_engine = workflow_engine
    application.state.memory_store = memory_store
    application.state.memory_manager = memory_manager
    application.state.action_router = router
    application.state.orchestrator = orchestrator
    application.state.activity_service = ActivityService(
        database,
        execution_history,
        audit_log,
        workflow_engine,
        approval_gate,
        tool_registry,
        rollback_manager,
        project_contexts,
    )

    from .observability.watchdog import SystemWatchdog
    watchdog = SystemWatchdog(config, workflow_engine, workflow_store, audit_log, laya_adapter)
    application.state.watchdog = watchdog

    application.include_router(health_router)
    application.include_router(tools_router)
    application.include_router(status_router)
    application.include_router(decisions_router)
    application.include_router(approvals_router)
    application.include_router(audit_router)
    application.include_router(files_router)
    application.include_router(rollback_router)
    application.include_router(workflows_router)
    application.include_router(memory_router)
    application.include_router(system2_router)
    application.include_router(activity_router)
    application.include_router(diagnostics_router)
    application.include_router(chat_router)

    @application.get("/", include_in_schema=False)
    async def dashboard_root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard/", status_code=307)

    @application.get("/dashboard", include_in_schema=False)
    async def dashboard_redirect() -> RedirectResponse:
        return RedirectResponse(url="/dashboard/", status_code=307)

    application.mount(
        "/dashboard",
        StaticFiles(directory=PROJECT_ROOT / "frontend", html=True, check_dir=True),
        name="dashboard",
    )

    @application.exception_handler(LayaAdapterError)
    async def laya_error(_request: Request, exc: LayaAdapterError) -> JSONResponse:
        from .systems.laya.exceptions import LayaNotConfiguredError
        code = "laya_not_configured" if isinstance(exc, LayaNotConfiguredError) else "laya_unavailable"
        return JSONResponse(status_code=503, content=ErrorResponse(code=code, message=str(exc)).model_dump(mode="json"))

    @application.exception_handler(ProjectContextError)
    async def project_context_error(_request: Request, exc: ProjectContextError) -> JSONResponse:
        return JSONResponse(status_code=403, content=ErrorResponse(code="project_not_authorized", message=str(exc)).model_dump(mode="json"))

    @application.exception_handler(DecisionLimitError)
    async def decision_limit_error(_request: Request, exc: DecisionLimitError) -> JSONResponse:
        return JSONResponse(status_code=422, content=ErrorResponse(code="iteration_limit_reached", message=str(exc)).model_dump(mode="json"))

    @application.exception_handler(ApprovalError)
    async def approval_error(_request: Request, exc: ApprovalError) -> JSONResponse:
        return JSONResponse(status_code=409, content=ErrorResponse(code="approval_conflict", message=str(exc)).model_dump(mode="json"))

    @application.exception_handler(PolicyDeniedError)
    async def policy_denied(_request: Request, exc: PolicyDeniedError) -> JSONResponse:
        return JSONResponse(status_code=403, content=ErrorResponse(code="policy_denied", message=str(exc)).model_dump(mode="json"))

    @application.exception_handler(ApprovalRequiredError)
    async def approval_required(_request: Request, exc: ApprovalRequiredError) -> JSONResponse:
        return JSONResponse(status_code=403, content=ErrorResponse(code="approval_required", message=str(exc)).model_dump(mode="json"))

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=ErrorResponse(code="request_validation_error", message="Request does not match the API schema.").model_dump(mode="json"))

    @application.exception_handler(Exception)
    async def unhandled_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled request error", exc_info=(type(exc), exc, exc.__traceback__))
        body = ErrorResponse(code="internal_error", message="The request could not be completed.")
        return JSONResponse(status_code=500, content=body.model_dump(mode="json"))

    return application


app = create_app()
