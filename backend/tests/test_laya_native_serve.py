"""Tests for the native laya.serve /v1/systemone wire protocol integration."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from backend.app.config import Settings
from backend.app.systems.laya.adapter import LayaAdapter
from backend.app.systems.laya.client import LayaClient
from backend.app.systems.laya.models import DecisionKind, DecisionRequest


def test_native_laya_serve_response_normalization() -> None:
    async def native_handler(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content)
        assert "state" in body
        assert "questions" in body
        assert body["model"] == "typed-decisions"
        return httpx.Response(200, json={
            "model": "laya-rl-agent",
            "answers": {
                "kind": {
                    "type": "choice",
                    "choice": "ACTION",
                    "probabilities": {"ACTION": 0.85, "NO_ACTION": 0.15},
                    "answer_confidence": 0.85,
                },
                "action_id": {
                    "type": "choice",
                    "choice": "file.read",
                    "probabilities": {"file.read": 0.90, "file.replace": 0.10},
                    "answer_confidence": 0.90,
                },
            },
            "usage": {"input_tokens": 40, "output_tokens": 0},
            "routing": {"model": "typed-decisions"},
        })

    async def run() -> None:
        settings = Settings(
            laya_decision_url="http://127.0.0.1:8000/v1/systemone",
            laya_timeout_seconds=30.0,
        )
        client = LayaClient(settings, transport=httpx.MockTransport(native_handler))
        adapter = LayaAdapter(settings, client)
        req = DecisionRequest(user_input="Please read file config.json", project_id="demo")
        result = await adapter.decide(req)

        assert result.decision.kind == DecisionKind.ACTION
        assert result.decision.action_id == "file.read"
        assert result.decision.parameters.get("path") == "config.json"
        assert result.decision.request_id == req.request_id
        assert result.runtime_configured is True
        assert "laya" in result.engine

    asyncio.run(run())
