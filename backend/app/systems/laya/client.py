"""HTTP transport for local and remote Laya decision endpoints.

Supports both:
1. Native `laya-serve` / Jev-compatible wire protocol (`POST /v1/systemone`):
   Sends state + questions dictionary and normalizes the calibrated answer choice into
   Laya Pro's DecisionEnvelope.
2. Normalized adapter protocol (`DecisionEnvelope` JSON directly).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx

from ...config import Settings
from .exceptions import LayaAuthError, LayaContractError, LayaNotConfiguredError, LayaRuntimeError
from .models import DecisionKind, DecisionRequest


class LayaClient:
    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._settings = settings
        self._transport = transport

    def _is_native_systemone_endpoint(self, endpoint: str) -> bool:
        return endpoint.rstrip("/").endswith("/v1/systemone")

    def _format_native_payload(self, request: DecisionRequest) -> dict[str, Any]:
        """Format request for laya.serve /v1/systemone endpoint."""
        state = {
            "body": request.user_input,
            "request_id": request.request_id,
            "project_id": request.project_id,
            "workflow_id": request.workflow_id,
            "iteration": request.iteration,
            "context": request.context,
        }
        questions = {
            "kind": {
                "type": "choice",
                "instructions": "Determine the decision kind for this user request.",
                "criteria": {
                    "ACTION": "The request asks to read, write, or rollback files, or take an active system action.",
                    "CLARIFICATION_REQUIRED": "The request is ambiguous, incomplete, or requires missing parameters.",
                    "REJECT": "The request is forbidden, dangerous, or violates safety rules.",
                    "NO_ACTION": "The request is purely conversational, greeting, or requires no tool execution.",
                },
            },
            "action_id": {
                "type": "choice",
                "instructions": "If an ACTION is requested, which registered tool should be invoked?",
                "criteria": {
                    "file.read": "Read or inspect file contents or path details.",
                    "file.replace": "Modify, create, write, or replace file contents.",
                    "file.rollback": "Roll back a prior file modification.",
                    "none": "No registered tool is needed for this request.",
                },
            },
        }
        return {
            "state": state,
            "questions": questions,
            "model": "typed-decisions",
        }

    def _extract_parameters_from_text(self, action_id: str, text: str) -> dict[str, Any]:
        """Extract tool parameters from input text safely using path heuristics."""
        params: dict[str, Any] = {}
        # Look for explicit path mentions
        path_match = re.search(r"([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9_]+)", text)
        if path_match:
            candidate_path = path_match.group(1).strip()
            # Clean up path
            candidate_path = candidate_path.lstrip("./").lstrip("/").lstrip("\\")
            params["path"] = candidate_path
        else:
            params["path"] = "target.txt"

        if action_id == "file.replace":
            params["content"] = text
            params["expected_sha256"] = None
        elif action_id == "file.rollback":
            op_match = re.search(r"(op_[a-zA-Z0-9_\-]+|operation_[a-zA-Z0-9_\-]+)", text)
            params["operation_id"] = op_match.group(1) if op_match else "operation_default"

        return params

    def _normalize_native_response(self, raw: dict[str, Any], request: DecisionRequest) -> dict[str, Any]:
        """Translate laya-serve Jev answers dictionary into Laya Pro DecisionEnvelope."""
        answers = raw.get("answers", {})
        kind_answer = answers.get("kind", {})
        action_answer = answers.get("action_id", {})

        kind_choice = kind_answer.get("choice", "NO_ACTION").upper()
        if kind_choice not in DecisionKind.__members__:
            kind_choice = "NO_ACTION"

        action_choice = action_answer.get("choice", "none")
        if action_choice == "none":
            action_id = None
        else:
            action_id = action_choice

        # Build valid parameters if operational
        parameters: dict[str, Any] = {}
        missing_information: list[str] = []

        if kind_choice in {"ACTION", "CONTINUE"}:
            if not action_id:
                # If kind is ACTION but action_id is none, default to read
                action_id = "file.read"
            parameters = self._extract_parameters_from_text(action_id, request.user_input)
            reason = f"Laya System 1 classified intent as {action_id} with calibrated confidence {kind_answer.get('answer_confidence', 0.9):.2f}"
        elif kind_choice == "CLARIFICATION_REQUIRED":
            action_id = None
            parameters = {}
            missing_information = ["target_path", "operation_details"]
            reason = "Laya System 1 determined that the request requires clarifying information"
        else:
            action_id = None
            parameters = {}
            reason = f"Laya System 1 concluded {kind_choice} (no tool action proposed)"

        model_name = raw.get("model", "laya-rl-agent")
        routing_model = raw.get("routing", {}).get("model", "typed-decisions") if isinstance(raw.get("routing"), dict) else "typed-decisions"

        return {
            "decision": {
                "kind": kind_choice,
                "reason": reason,
                "action_id": action_id,
                "parameters": parameters,
                "relevant_context": request.context,
                "missing_information": missing_information,
                "request_id": request.request_id,
            },
            "engine": f"laya/{model_name}:{routing_model}",
            "runtime_configured": True,
        }

    async def decide(self, request: DecisionRequest) -> dict[str, Any]:
        endpoint = self._settings.laya_decision_url
        if endpoint is None:
            raise LayaNotConfiguredError("No verified loopback Laya decision endpoint is configured")
        if len(request.user_input) > self._settings.laya_max_input_chars:
            raise LayaContractError("Input exceeds the configured Laya request limit")

        is_native = self._is_native_systemone_endpoint(endpoint)
        if is_native:
            payload = self._format_native_payload(request)
        else:
            payload = request.model_dump(mode="json")

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._settings.laya_api_key:
            headers["Authorization"] = f"Bearer {self._settings.laya_api_key}"

        try:
            async with httpx.AsyncClient(
                timeout=self._settings.laya_timeout_seconds,
                transport=self._transport,
                follow_redirects=False,
                headers=headers,
            ) as client:
                async with client.stream("POST", endpoint, json=payload) as response:
                    if response.status_code in {401, 403}:
                        raise LayaAuthError(f"Laya runtime authentication failed: HTTP {response.status_code}")
                    if response.status_code < 200 or response.status_code >= 300:
                        raise LayaRuntimeError(f"Laya runtime returned HTTP {response.status_code}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self._settings.laya_max_output_bytes:
                            raise LayaContractError("Laya runtime response exceeded configured size limit")
        except (LayaRuntimeError, LayaContractError):
            raise
        except httpx.TimeoutException as exc:
            raise LayaRuntimeError("Laya runtime request timed out") from exc
        except httpx.HTTPError as exc:
            raise LayaRuntimeError("Laya runtime request failed") from exc

        try:
            parsed = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LayaContractError("Laya runtime response was not valid JSON") from exc

        if not isinstance(parsed, dict):
            raise LayaContractError("Laya runtime response must be a JSON object")

        if is_native:
            return self._normalize_native_response(parsed, request)

        return parsed
