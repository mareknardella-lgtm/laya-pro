"""Independent, deny-by-default policy evaluation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from ..config import Settings
from ..tools.fingerprint import input_fingerprint
from ..tools.models import RiskLevel, ToolAuthorization
from ..tools.registry import ToolDefinition
from .approval_gate import ApprovalError, ApprovalGate


class PolicyDeniedError(PermissionError):
    """Policy denied an operation; this is never resolved by System 2."""


class ApprovalRequiredError(PermissionError):
    """An explicit, exact-action human approval is required."""


@dataclass(frozen=True)
class PolicyEvaluation:
    allowed: bool
    requires_approval: bool
    reason: str
    input_fingerprint: str


class PolicyEngine:
    def __init__(self, settings: Settings, approval_gate: Optional[ApprovalGate] = None) -> None:
        self._settings = settings
        self._approval_gate = approval_gate

    def evaluate(self, definition: ToolDefinition, parameters: dict, context: object = None) -> PolicyEvaluation:
        validated = definition.input_model.model_validate(parameters)
        normalized = validated.model_dump(mode="json")
        project_context = getattr(context, "project", None) if context is not None else None
        workflow_context = getattr(context, "workflow", None) if context is not None else None
        project_id = getattr(project_context, "project_id", None) or getattr(context, "project_id", None)
        workflow_id = getattr(workflow_context, "workflow_id", None) or getattr(context, "workflow_id", None)
        fingerprint = input_fingerprint(definition.tool_id, normalized, project_id, workflow_id)
        if not definition.required_permissions.issubset(self._settings.allowed_permissions):
            return PolicyEvaluation(False, False, "Required permissions are not granted", fingerprint)
        if any(permission.startswith("project:") for permission in definition.required_permissions) and project_context is None:
            return PolicyEvaluation(False, False, "Project-scoped operation requires configured project context", fingerprint)
        if any(permission.startswith("workflow:") for permission in definition.required_permissions):
            if workflow_context is None or workflow_context.workflow_id is None:
                return PolicyEvaluation(False, False, "Workflow-scoped operation requires workflow context", fingerprint)
        for prerequisite in definition.prerequisites:
            if prerequisite == "project" and project_context is None:
                return PolicyEvaluation(False, False, "Tool prerequisite project is unavailable", fingerprint)
            if prerequisite == "workflow" and (workflow_context is None or workflow_context.workflow_id is None):
                return PolicyEvaluation(False, False, "Tool prerequisite workflow is unavailable", fingerprint)
            if prerequisite not in {"project", "workflow"}:
                return PolicyEvaluation(False, False, "Tool declares an unsupported prerequisite", fingerprint)
        requires_approval = definition.risk_level != RiskLevel.LOW
        return PolicyEvaluation(True, requires_approval, "Allowed by explicit permission grant", fingerprint)

    def authorize(
        self,
        definition: ToolDefinition,
        parameters: dict,
        context: object,
        fingerprint: str,
        approval_id: Optional[str] = None,
        action_ref: Optional[str] = None,
    ) -> ToolAuthorization:
        validated = definition.input_model.model_validate(parameters)
        normalized = validated.model_dump(mode="json")
        project_context = getattr(context, "project", None) if context is not None else None
        workflow_context = getattr(context, "workflow", None) if context is not None else None
        project_id = getattr(project_context, "project_id", None) or getattr(context, "project_id", None)
        workflow_id = getattr(workflow_context, "workflow_id", None) or getattr(context, "workflow_id", None)
        expected_fingerprint = (
            input_fingerprint(definition.tool_id, {"parameters": normalized, "action_ref": action_ref}, project_id, workflow_id)
            if action_ref is not None
            else input_fingerprint(definition.tool_id, normalized, project_id, workflow_id)
        )
        if expected_fingerprint != fingerprint:
            raise PolicyDeniedError("Parameters or action reference changed after policy validation")
        evaluation = self.evaluate(definition, normalized, context)
        if not evaluation.allowed:
            raise PolicyDeniedError(evaluation.reason)
        if evaluation.requires_approval:
            if self._approval_gate is None or approval_id is None:
                raise ApprovalRequiredError("This risk level requires an explicit approval for the exact action")
            try:
                return self._approval_gate.consume(
                    approval_id,
                    definition,
                    fingerprint,
                    project_id,
                    workflow_id,
                    normalized,
                    action_ref,
                )
            except ApprovalError as exc:
                raise PolicyDeniedError(str(exc)) from exc
        return ToolAuthorization(
            tool_id=definition.tool_id,
            input_fingerprint=fingerprint,
            permissions=definition.required_permissions,
            expires_at_epoch=time.time() + 30,
        )
