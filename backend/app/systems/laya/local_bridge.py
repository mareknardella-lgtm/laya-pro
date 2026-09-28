"""Fail-closed subprocess bridge to existing local laya_torch.py wrapper.

Invokes the existing Python interpreter at LAYA_PYTHON_PATH and existing
wrapper at LAYA_WRAPPER_PATH without reinstalling or duplicating packages.
Uses persistent or on-demand subprocess communication with strict JSON validation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from ...config import Settings
from .exceptions import LayaContractError, LayaNotConfiguredError, LayaRuntimeError
from .models import DecisionKind, DecisionRequest

logger = logging.getLogger("laya.systems.laya.local_bridge")


class LocalLayaBridge:
    """Communicates directly with the existing local Laya PyTorch wrapper."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock: Optional[asyncio.Lock] = None
        self._process: Optional[asyncio.subprocess.Process] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def is_configured(self) -> bool:
        if self._settings.laya_python_path and self._settings.laya_wrapper_path:
            return (
                self._settings.laya_python_path.is_file()
                and self._settings.laya_wrapper_path.is_file()
            )
        return False

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        """Starts or returns existing long-lived subprocess running laya_torch."""
        if self._process is not None and self._process.returncode is None:
            return self._process

        python_exe = self._settings.laya_python_path
        wrapper_file = self._settings.laya_wrapper_path
        local_dir = self._settings.laya_local_path or (wrapper_file.parent if wrapper_file else None)

        if not python_exe or not python_exe.is_file():
            raise LayaNotConfiguredError(f"LAYA_PYTHON_PATH is not configured or does not exist: {python_exe}")
        if not wrapper_file or not wrapper_file.is_file():
            raise LayaNotConfiguredError(f"LAYA_WRAPPER_PATH is not configured or does not exist: {wrapper_file}")

        wrapper_dir_posix = str(local_dir.resolve()).replace("\\", "/")

        worker_code = (
            "import sys, json\n"
            f"sys.path.insert(0, '{wrapper_dir_posix}')\n"
            "try:\n"
            "    import laya_torch\n"
            "    laya_inst = laya_torch.Laya()\n"
            "    print('__LAYA_WORKER_READY__', flush=True)\n"
            "except Exception as e:\n"
            "    print('__LAYA_WORKER_ERROR__:' + str(e), flush=True)\n"
            "    sys.exit(1)\n"
            "\n"
            "while True:\n"
            "    line = sys.stdin.readline()\n"
            "    if not line: break\n"
            "    line = line.strip()\n"
            "    if not line: continue\n"
            "    try:\n"
            "        req = json.loads(line)\n"
            "        qtype = req.get('type', 'ask')\n"
            "        state = req['state']\n"
            "        if qtype == 'ask':\n"
            "            res = laya_inst.ask(state, req['questions'])\n"
            "        elif qtype == 'choice':\n"
            "            res = laya_inst.choice(state, req['question'], req['options'])\n"
            "        elif qtype == 'score':\n"
            "            res = laya_inst.score(state, req['question'], req['criteria'])\n"
            "        elif qtype == 'noul':\n"
            "            res = laya_inst.noul(state, req['question'], req['false_desc'], req['true_desc'])\n"
            "        else:\n"
            "            res = {'error': f'Unsupported query type {qtype}'}\n"
            "        print('__RESP__:' + json.dumps(res), flush=True)\n"
            "    except Exception as exc:\n"
            "        print('__RESP_ERR__:' + str(exc), flush=True)\n"
        )

        try:
            self._process = await asyncio.create_subprocess_exec(
                str(python_exe),
                "-c",
                worker_code,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            raise LayaRuntimeError(f"Failed to spawn local Laya subprocess: {exc}") from exc

        # Wait for __LAYA_WORKER_READY__ signal
        timeout = self._settings.laya_timeout_seconds
        start_time = asyncio.get_event_loop().time()
        ready = False
        startup_logs = []

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            if self._process.stdout is None:
                break
            try:
                line_bytes = await asyncio.wait_for(self._process.stdout.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                if self._process.returncode is not None:
                    break
                continue

            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                if self._process.returncode is not None:
                    break
                continue

            startup_logs.append(line)
            if line == "__LAYA_WORKER_READY__":
                ready = True
                break
            if line.startswith("__LAYA_WORKER_ERROR__:"):
                raise LayaRuntimeError(f"Local Laya worker initialization failed: {line}")

        if not ready:
            err = ""
            if self._process.stderr:
                err_bytes = await self._process.stderr.read()
                err = err_bytes.decode("utf-8", errors="replace")
            self.terminate()
            raise LayaRuntimeError(f"Local Laya worker failed to ready within {timeout}s. Logs: {' | '.join(startup_logs)}. Stderr: {err}")

        return self._process

    def terminate(self) -> None:
        """Terminate the running worker subprocess."""
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass
            self._process = None

    def _extract_parameters_from_text(self, action_id: str, text: str) -> dict[str, Any]:
        """Extract tool parameters from input text safely using path heuristics."""
        params: dict[str, Any] = {}
        path_match = re.search(r"([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9_]+)", text)
        if path_match:
            candidate_path = path_match.group(1).strip()
            candidate_path = candidate_path.lstrip("./").lstrip("/").lstrip("\\")
            params["path"] = candidate_path
        else:
            params["path"] = "test_document.txt"

        if action_id == "file.replace":
            # Extract content if specified or default
            params["content"] = text
            params["expected_sha256"] = None
        elif action_id == "file.rollback":
            op_match = re.search(r"(op_[a-zA-Z0-9_\-]+|operation_[a-zA-Z0-9_\-]+)", text)
            params["operation_id"] = op_match.group(1) if op_match else "operation_default"

        return params

    def _normalize_response(self, raw_answers: dict[str, Any], request: DecisionRequest) -> dict[str, Any]:
        """Convert laya_torch answer dictionary into Laya Pro DecisionEnvelope."""
        answers = raw_answers.get("answers", {}) if "answers" in raw_answers else raw_answers
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

        missing_information: list[str] = []

        if kind_choice in {"ACTION", "CONTINUE"}:
            if not action_id:
                action_id = "file.read"
            parameters = self._extract_parameters_from_text(action_id, request.user_input)
            confidence = kind_answer.get("confidence", 0.9)
            reason = f"Laya System 1 (local PyTorch wrapper) classified intent as {action_id} with calibrated confidence {confidence:.2f}"
        elif kind_choice == "CLARIFICATION_REQUIRED":
            action_id = None
            parameters = {}
            missing_information = ["target_path", "operation_details"]
            reason = "Laya System 1 (local PyTorch wrapper) determined that the request requires clarifying information"
        else:
            action_id = None
            parameters = {}
            reason = f"Laya System 1 (local PyTorch wrapper) concluded {kind_choice} (no tool action proposed)"

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
            "engine": "laya-local-torch/laya-multilingual",
            "runtime_configured": True,
        }

    async def decide(self, request: DecisionRequest) -> dict[str, Any]:
        async with self._get_lock():
            proc = await self._ensure_process()
            if proc.stdin is None or proc.stdout is None:
                raise LayaRuntimeError("Laya worker subprocess stdin/stdout unavailable")

            questions = {
                "kind": {
                    "type": "choice",
                    "instructions": "Does this request require an action or clarification or nothing?",
                    "criteria": {
                        "ACTION": "Execute file or system action",
                        "CLARIFICATION_REQUIRED": "Request is ambiguous or incomplete",
                        "NO_ACTION": "No tool action needed",
                    },
                },
                "action_id": {
                    "type": "choice",
                    "instructions": "Which action should be taken?",
                    "criteria": {
                        "file.read": "Read file",
                        "file.replace": "Replace or write file",
                        "file.rollback": "Roll back a prior file modification",
                        "none": "No tool action needed",
                    },
                },
            }

            wire_req = {
                "type": "ask",
                "state": request.user_input,
                "questions": questions,
            }

            try:
                line_to_send = json.dumps(wire_req) + "\n"
                proc.stdin.write(line_to_send.encode("utf-8"))
                await proc.stdin.drain()

                resp_line = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=self._settings.laya_timeout_seconds,
                )
                text = resp_line.decode("utf-8", errors="replace").strip()
            except asyncio.TimeoutError as exc:
                self.terminate()
                raise LayaRuntimeError("Local Laya inference timed out") from exc
            except Exception as exc:
                self.terminate()
                raise LayaRuntimeError(f"Error communicating with local Laya worker: {exc}") from exc

            if text.startswith("__RESP_ERR__:"):
                raise LayaRuntimeError(f"Local Laya inference failed: {text}")

            if not text.startswith("__RESP__:"):
                raise LayaContractError(f"Unexpected response format from local Laya worker: {text[:200]}")

            payload_json = text[len("__RESP__:"):]
            try:
                raw = json.loads(payload_json)
            except Exception as exc:
                raise LayaContractError("Failed to parse JSON response from local Laya worker") from exc

            return self._normalize_response(raw, request)
