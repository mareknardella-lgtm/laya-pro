"""Safe operator projections from actual decisions, workflows, executions, and approvals."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..changes.rollback import FileOperationError, RollbackManager
from ..context.project_context import ProjectContextError, ProjectContextManager
from ..security.approval_gate import ApprovalError, ApprovalGate, ApprovalRequest, ApprovalStatus
from ..storage.sqlite import SQLiteDatabase
from ..tools.models import RiskLevel
from ..tools.registry import ToolRegistry, ToolRegistryError
from ..workflows.engine import WorkflowEngine
from ..workflows.models import WorkflowRecord, WorkflowStatus
from ..workflows.state import WorkflowStateError
from .audit import AuditLog, project_audit_scope_predicate
from .executions import (
    ExecutionDiagnosticEvent,
    ExecutionDetail,
    ExecutionHistoryStatus,
    ExecutionHistoryStore,
    ExecutionPage,
    WorkflowExecutionSummary,
)
from .safe_projection import safe_parameters


class DecisionHistoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    request_id: str
    workflow_id: Optional[str]
    project_id: Optional[str]
    created_at: datetime
    status: str
    kind: str
    action_id: Optional[str]
    execution_status: str


class DecisionPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DecisionHistoryRecord]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class ApprovalValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    message: str


class ApprovalDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval: ApprovalRequest
    validation: ApprovalValidation
    parameters_preview: dict
    operation_id: Optional[str] = None
    associated_workflow_status: Optional[str] = None


class WorkflowStepSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    tool_id: str
    depends_on: list[str]
    status: str
    execution_id: Optional[str] = None
    execution_status: Optional[str] = None
    approval_id: Optional[str] = None
    approval_status: Optional[str] = None
    approval_expires_at: Optional[datetime] = None


class WorkflowEventSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    created_at: datetime
    event_type: str
    status: str
    resource_id: Optional[str] = None


class OperatorWorkflowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    project_id: Optional[str]
    status: WorkflowStatus
    created_at: datetime
    updated_at: datetime
    version: int
    pending_step: Optional[str] = None
    steps: list[WorkflowStepSummary]
    completed_steps: list[str]
    error_code: Optional[str] = None
    is_critical: bool = False
    events: list[WorkflowEventSummary] = Field(default_factory=list)


class ActivityService:
    def __init__(
        self,
        database: SQLiteDatabase,
        executions: ExecutionHistoryStore,
        audit: AuditLog,
        workflows: WorkflowEngine,
        approvals: ApprovalGate,
        registry: ToolRegistry,
        rollback_manager: RollbackManager,
        projects: ProjectContextManager,
    ) -> None:
        self._database = database
        self._executions = executions
        self._audit = audit
        self._workflows = workflows
        self._approvals = approvals
        self._registry = registry
        self._rollback_manager = rollback_manager
        self._projects = projects

    def _scoped_project_ids(self, project_ids: Optional[list[str]] = None) -> list[str]:
        authorized = self._projects.project_ids
        requested = authorized if project_ids is None else set(project_ids)
        return sorted(authorized.intersection(requested))[:900]

    def projects(self) -> list[str]:
        return self._scoped_project_ids()

    def approvals(self, limit: int = 100, project_ids: Optional[list[str]] = None) -> list[ApprovalDetail]:
        bounded = max(1, min(limit, 200))
        authorized_projects = self._scoped_project_ids(project_ids)
        if not authorized_projects:
            return []
        marks = ",".join("?" for _ in authorized_projects)
        now = datetime.now(timezone.utc).isoformat()
        with self._database.transaction() as connection:
            connection.execute(
                f"UPDATE approvals SET status = 'expired' WHERE status = 'pending' AND expires_at <= ? AND project_id IN ({marks})",
                [now, *authorized_projects],
            )
            rows = connection.execute(
                f"SELECT * FROM approvals WHERE project_id IN ({marks}) ORDER BY created_at DESC LIMIT ?",
                [*authorized_projects, bounded],
            ).fetchall()
        return [self.approval_detail(self._approvals._from_row(row)) for row in rows]

    def approval_detail(self, source: ApprovalRequest) -> ApprovalDetail:
        source = source.model_copy(update={"reason": safe_parameters(source.reason)})
        approval = source.model_copy(update={"parameters_preview": safe_parameters(source.parameters_preview)})
        now = datetime.now(timezone.utc)
        operation_id = source.parameters_preview.get("operation_id")
        if source.expires_at <= now:
            validation = ApprovalValidation(status="expired", message="Approval expiration has passed. The backend will not allow this approval to authorize an action.")
        elif source.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
            validation = ApprovalValidation(status=source.status.value, message=f"Approval is {source.status.value}; it cannot authorize a new action.")
        elif source.workflow_id is not None:
            validation = self._validate_workflow_approval(source)
        elif source.tool_id == "file.rollback":
            validation = self._validate_rollback_approval(source, operation_id)
        else:
            validation = ApprovalValidation(
                status="backend_recorded",
                message="Backend record is available. The complete parameter set is not persisted; exact fingerprint, scope, expiry and single-use are rechecked before execution.",
            )
        parameter_source: dict = {}
        workflow_status = self._workflow_status(source.workflow_id, source.project_id)
        if source.workflow_id is not None:
            try:
                workflow = self._workflows.get(source.workflow_id)
                if (
                    workflow.project_id == source.project_id
                    and workflow.status in {WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.PAUSED}
                ):
                    self._projects.resolve(workflow.project_id)
                    pending_id = workflow.state.get("pending_step")
                    step = next((item for item in workflow.definition.steps if item.step_id == pending_id), None)
                    linked_id = workflow.state.get("approval_requests", {}).get(pending_id)
                    if step is not None and step.tool_id == source.tool_id and linked_id == source.approval_id:
                        parameter_source = step.parameters
            except (ProjectContextError, WorkflowStateError):
                parameter_source = {}
        elif source.tool_id == "file.rollback" and isinstance(operation_id, str):
            parameter_source = {"operation_id": operation_id}
        return ApprovalDetail(
            approval=approval,
            validation=validation,
            parameters_preview=safe_parameters(parameter_source) if parameter_source else approval.parameters_preview,
            operation_id=str(operation_id) if operation_id is not None else None,
            associated_workflow_status=workflow_status,
        )

    def _validate_workflow_approval(self, approval: ApprovalRequest) -> ApprovalValidation:
        try:
            workflow = self._workflows.get(approval.workflow_id)
            self._projects.resolve(workflow.project_id)
        except (WorkflowStateError, ProjectContextError):
            return ApprovalValidation(status="unavailable", message="Associated workflow is unavailable or no longer authorized; execution will not be authorized.")
        pending_id = workflow.state.get("pending_step")
        step = next((item for item in workflow.definition.steps if item.step_id == pending_id), None)
        linked_id = workflow.state.get("approval_requests", {}).get(pending_id)
        allowed = workflow.status in {WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.PAUSED}
        try:
            definition = self._registry.get(step.tool_id) if step else None
            action_ref = step.parameters.get("operation_id") if step and step.tool_id == "file.rollback" else None
            expected = ApprovalGate.fingerprint(definition, step.parameters, workflow.project_id, workflow.workflow_id, action_ref) if step and definition else None
            exact = bool(step and definition and linked_id == approval.approval_id and workflow.project_id == approval.project_id and step.tool_id == approval.tool_id and expected == approval.input_fingerprint)
        except (ToolRegistryError, ValueError, TypeError):
            exact = False
        if allowed and exact:
            return ApprovalValidation(status="valid", message="Backend confirms the approval matches this workflow's exact pending step and project. Execution rechecks expiry and one-time use.")
        return ApprovalValidation(status="invalid", message="Workflow is not awaiting this exact pending action. This approval cannot authorize the displayed workflow step.")

    def _validate_rollback_approval(self, approval: ApprovalRequest, operation_id: object) -> ApprovalValidation:
        if not isinstance(operation_id, str) or not operation_id:
            return ApprovalValidation(status="invalid", message="Rollback operation ID is unavailable in the safe preview.")
        try:
            record = self._rollback_manager.get_record(operation_id)
            definition = self._registry.get("file.rollback")
            expected = ApprovalGate.fingerprint(definition, {"operation_id": operation_id}, approval.project_id, None, operation_id)
        except (FileOperationError, ToolRegistryError, ValueError):
            return ApprovalValidation(status="invalid", message="Rollback operation is unavailable; this approval cannot authorize it.")
        if record.project_id != approval.project_id or record.status != "applied" or expected != approval.input_fingerprint:
            return ApprovalValidation(status="invalid", message="Rollback approval does not match the exact applied operation and project scope.")
        return ApprovalValidation(status="valid", message="Backend confirms this approval is bound to this exact applied operation. Execution rechecks expiry and one-time use.")

    def _workflow_status(self, workflow_id: Optional[str], project_id: Optional[str]) -> Optional[str]:
        if not workflow_id:
            return None
        try:
            workflow = self._workflows.get(workflow_id)
            self._projects.resolve(workflow.project_id)
            if workflow.project_id != project_id:
                return "unavailable"
            return workflow.status.value
        except (WorkflowStateError, ProjectContextError):
            return "unavailable"

    def executions(self, *, limit: int = 50, offset: int = 0, query: Optional[str] = None, status: Optional[ExecutionHistoryStatus] = None, workflow_id: Optional[str] = None, since: Optional[datetime] = None, until: Optional[datetime] = None, project_ids: Optional[list[str]] = None) -> ExecutionPage:
        scoped_projects = self._scoped_project_ids(project_ids)
        records, total = self._executions.list(limit=limit, offset=offset, query=query, status=status, workflow_id=workflow_id, since=since, until=until, project_ids=scoped_projects)
        return ExecutionPage(items=records, total=total, limit=max(1, min(limit, 200)), offset=max(0, min(offset, 100_000)))

    def execution(self, execution_id: str) -> ExecutionDetail:
        record = self._executions.get(execution_id)
        workflow_summary = None
        if record.project_id is None:
            raise WorkflowStateError("Execution project scope is unavailable")
        try:
            self._projects.resolve(record.project_id)
        except ProjectContextError as exc:
            raise WorkflowStateError("Execution project is no longer authorized") from exc
        workflow = None
        workflow_link_conflict = False
        if record.workflow_id:
            try:
                candidate = self._workflows.get(record.workflow_id)
            except WorkflowStateError:
                candidate = None
            if candidate is not None:
                if candidate.project_id != record.project_id:
                    workflow_link_conflict = True
                else:
                    try:
                        self._projects.resolve(candidate.project_id)
                        workflow = candidate
                    except ProjectContextError:
                        workflow_link_conflict = True
            if workflow is not None:
                workflow_summary = WorkflowExecutionSummary(
                    workflow_id=workflow.workflow_id,
                    project_id=workflow.project_id,
                    status=workflow.status.value,
                    created_at=workflow.created_at,
                    updated_at=workflow.updated_at,
                    pending_step=workflow.state.get("pending_step"),
                    steps=[{"step_id": step.step_id, "tool_id": step.tool_id} for step in workflow.definition.steps],
                )
        audit_events: list[dict] = []
        if workflow is not None:
            audit_events = self._audit.list_events(
                limit=200,
                workflow_id=record.workflow_id,
                project_ids=[record.project_id],
            )
        audit_events.extend(self._audit.list_execution_events(
            record.execution_id,
            record.project_id,
            expected_workflow_id=record.workflow_id if not workflow_link_conflict else None,
            trust_execution_link=workflow_link_conflict,
            limit=200,
        ))
        audit_events = list({event["event_id"]: event for event in audit_events}.values())
        return ExecutionDetail(
            execution=record,
            workflow=workflow_summary,
            events=[ExecutionDiagnosticEvent(
                event_id=event["event_id"],
                created_at=datetime.fromisoformat(event["created_at"]),
                event_type=event["event_type"],
                workflow_id=event["workflow_id"],
                execution_id=event["execution_id"],
                resource_id=event["resource_id"],
                status=event["status"],
            ) for event in audit_events if (
                event["execution_id"] == record.execution_id
                or (
                    record.workflow_id is not None
                    and not workflow_link_conflict
                    and event["workflow_id"] == record.workflow_id
                )
            )],
        )

    def decisions(self, limit: int = 50, offset: int = 0, project_ids: Optional[list[str]] = None) -> DecisionPage:
        return list_decisions(self._database, limit, offset, project_ids=self._scoped_project_ids(project_ids))

    def record_decision(self, result: object) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO decision_events (
                    decision_id, request_id, workflow_id, project_id, created_at, status, kind, action_id, execution_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (result.decision_id, result.request_id, result.workflow_id, result.project_id, datetime.now(timezone.utc).isoformat(), result.status.value, result.kind.value, result.action_id, result.execution_status),
            )

    def workflows(self, limit: int = 100, project_ids: Optional[list[str]] = None) -> list[WorkflowRecord]:
        scoped_projects = self._scoped_project_ids(project_ids)
        if not scoped_projects:
            return []
        return self._workflows.list(max(1, min(limit, 200)), project_ids=scoped_projects)

    def workflow_summary(self, record: WorkflowRecord, include_events: bool = True) -> OperatorWorkflowSummary:
        state = record.state
        completed = set(state.get("completed_steps", []))
        result_map = state.get("step_results", {})
        pending_id = state.get("pending_step")
        approvals = state.get("approval_requests", {})
        steps: list[WorkflowStepSummary] = []
        for step in record.definition.steps:
            result = result_map.get(step.step_id, {})
            approval = None
            try:
                if approvals.get(step.step_id):
                    candidate = self._approvals.get(approvals[step.step_id])
                    definition = self._registry.get(step.tool_id)
                    action_ref = step.parameters.get("operation_id") if step.tool_id == "file.rollback" else None
                    expected = ApprovalGate.fingerprint(
                        definition,
                        step.parameters,
                        record.project_id,
                        record.workflow_id,
                        action_ref,
                    )
                    if (
                        candidate.project_id == record.project_id
                        and candidate.workflow_id == record.workflow_id
                        and candidate.tool_id == step.tool_id
                        and candidate.input_fingerprint == expected
                    ):
                        approval = candidate
            except (ApprovalError, ToolRegistryError, ValueError, TypeError, WorkflowStateError):
                pass
            step_status = "completed" if step.step_id in completed else "awaiting_approval" if step.step_id == pending_id else str(result.get("status") or "queued")
            steps.append(WorkflowStepSummary(
                step_id=step.step_id,
                tool_id=step.tool_id,
                depends_on=list(step.depends_on),
                status=step_status,
                execution_id=str(result["execution_id"]) if result.get("execution_id") else None,
                execution_status=str(result["status"]) if result.get("status") else None,
                approval_id=approval.approval_id if approval else None,
                approval_status=approval.status.value if approval else None,
                approval_expires_at=approval.expires_at if approval else None,
            ))
        events = []
        if include_events:
            events = [WorkflowEventSummary(event_id=event["event_id"], created_at=datetime.fromisoformat(event["created_at"]), event_type=event["event_type"], status=event["status"], resource_id=event["resource_id"]) for event in self._audit.list_events(limit=200, workflow_id=record.workflow_id, project_ids=[record.project_id] if record.project_id else [])]
        error_code = state.get("error")
        if not isinstance(error_code, str) or len(error_code) > 128:
            error_code = None
        elif not re.fullmatch(r"[A-Za-z0-9_.:-]+", error_code):
            error_code = "workflow_error"

        # A workflow is critical when at least one step needs an explicit operator
        # approval. This reuses the canonical policy rule (requires_approval is
        # risk_level != LOW in security.policy_engine) instead of inventing a second
        # classification, so low-risk read-only workflows never raise a priority alert.
        # An unresolvable tool fails safe as critical rather than silently unprotected.
        is_critical = False
        for step in record.definition.steps:
            try:
                defn = self._registry.get(step.tool_id)
            except Exception:
                is_critical = True
                break
            if defn.risk_level != RiskLevel.LOW:
                is_critical = True
                break

        return OperatorWorkflowSummary(
            workflow_id=record.workflow_id,
            project_id=record.project_id,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            version=record.version,
            pending_step=pending_id if isinstance(pending_id, str) else None,
            steps=steps,
            completed_steps=[step_id for step_id in state.get("completed_steps", []) if isinstance(step_id, str)],
            error_code=error_code,
            is_critical=is_critical,
            events=events,
        )

    def diagnostics(self, project_ids: Optional[list[str]] = None) -> dict:
        authorized_projects = self._scoped_project_ids(project_ids)
        with closing(self._database.connect()) as connection:
            if authorized_projects:
                marks = ",".join("?" for _ in authorized_projects)
                execution_counts = {
                    row["status"]: row["count"]
                    for row in connection.execute(
                        f"SELECT status, COUNT(*) AS count FROM execution_records WHERE project_id IN ({marks}) GROUP BY status",
                        authorized_projects,
                    ).fetchall()
                }
                last_execution = connection.execute(
                    f"SELECT MAX(completed_at) AS completed_at FROM execution_records WHERE status = 'succeeded' AND project_id IN ({marks})",
                    authorized_projects,
                ).fetchone()["completed_at"]
                pending = connection.execute(
                    f"SELECT COUNT(*) AS count FROM approvals WHERE status = 'pending' AND expires_at > ? AND project_id IN ({marks})",
                    [datetime.now(timezone.utc).isoformat(), *authorized_projects],
                ).fetchone()["count"]
                active = connection.execute(
                    f"SELECT COUNT(*) AS count FROM workflow_records WHERE status IN ('created','running','paused','awaiting_approval') AND project_id IN ({marks})",
                    authorized_projects,
                ).fetchone()["count"]
                decisions = connection.execute(
                    f"SELECT COUNT(*) AS count FROM decision_events WHERE project_id IN ({marks})",
                    authorized_projects,
                ).fetchone()["count"]
                audit_cte = f"WITH project_scope(project_id) AS (VALUES {','.join('( ? )' for _ in authorized_projects)}) "
                audit_scope = project_audit_scope_predicate()
                audit_values = list(authorized_projects)
                audit_count = connection.execute(f"{audit_cte}SELECT COUNT(*) AS count FROM audit_events WHERE {audit_scope}", audit_values).fetchone()["count"]
                latest_audit = connection.execute(f"{audit_cte}SELECT MAX(created_at) AS created_at FROM audit_events WHERE {audit_scope}", audit_values).fetchone()["created_at"]
            else:
                execution_counts = {}
                last_execution = None
                pending = active = decisions = audit_count = 0
                latest_audit = None
            health = connection.execute("SELECT MAX(checked_at) AS checked_at FROM system_health_checks WHERE api_status = 'healthy'").fetchone()["checked_at"]
        return {
            "observed_at": datetime.now(timezone.utc),
            "api_status": "healthy",
            "last_successful_health_check": datetime.fromisoformat(health) if health else None,
            "latest_audit_event_at": datetime.fromisoformat(latest_audit) if latest_audit else None,
            "last_successful_execution_at": datetime.fromisoformat(last_execution) if last_execution else None,
            "audit_event_count": int(audit_count),
            "decision_count": int(decisions),
            "pending_approval_count": int(pending),
            "active_workflow_count": int(active),
            "execution_counts": execution_counts,
        }


def list_decisions(
    database: SQLiteDatabase,
    limit: int = 50,
    offset: int = 0,
    *,
    project_ids: Optional[list[str]] = None,
) -> DecisionPage:
    bounded_limit = max(1, min(limit, 200))
    bounded_offset = max(0, min(offset, 100_000))
    clauses: list[str] = []
    values: list[object] = []
    if project_ids is not None:
        scoped_ids = list(dict.fromkeys(project_ids))[:900]
        if scoped_ids:
            clauses.append(f"project_id IN ({','.join('?' for _ in scoped_ids)})")
            values.extend(scoped_ids)
        else:
            clauses.append("1 = 0")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with closing(database.connect()) as connection:
        total = connection.execute(f"SELECT COUNT(*) AS count FROM decision_events {where}", values).fetchone()["count"]
        rows = connection.execute(
            f"SELECT * FROM decision_events {where} ORDER BY created_at DESC, decision_id DESC LIMIT ? OFFSET ?",
            [*values, bounded_limit, bounded_offset],
        ).fetchall()
    return DecisionPage(items=[DecisionHistoryRecord(
        decision_id=row["decision_id"],
        request_id=row["request_id"],
        workflow_id=row["workflow_id"],
        project_id=row["project_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        status=row["status"],
        kind=row["kind"],
        action_id=row["action_id"],
        execution_status=row["execution_status"],
    ) for row in rows], total=int(total), limit=bounded_limit, offset=bounded_offset)
