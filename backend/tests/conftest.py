"""Shared fixtures: stub runtimes for both engines, so tests never touch the network."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from backend.app.chat.history import ChatHistoryManager
from backend.app.chat.orchestrator import HybridOrchestrator
from backend.app.config import Settings
from backend.app.container import Container
from backend.app.main import create_app
from backend.app.memory.manager import MemoryManager
from backend.app.memory.store import MemoryStore
from backend.app.storage.sqlite import Database
from backend.app.systems.laya.adapter import LayaAdapter
from backend.app.systems.laya.client import LayaClient
from backend.app.systems.nemotron.adapter import NemotronAdapter
from backend.app.systems.nemotron.client import NemotronClient


def run(coroutine):
    """Run one coroutine to completion (no pytest-asyncio in this project)."""

    return asyncio.run(coroutine)


class StubNemotronClient(NemotronClient):
    """Returns canned completions and records every request it receives."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.replies: List[str] = []
        self.calls: List[Any] = []
        self.error: Optional[Exception] = None

    def queue(self, *replies: str) -> "StubNemotronClient":
        self.replies.extend(replies)
        return self

    async def generate(self, request: Any) -> Dict[str, Any]:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        text = self.replies.pop(0) if self.replies else "risposta generica"
        return {
            "request_id": request.request_id,
            "text": text,
            "engine": "stub-nemotron",
        }

    @property
    def roles(self) -> List[str]:
        return [call.role.value for call in self.calls]

    def calls_of(self, role: str) -> List[Any]:
        """Filter out background memory-extraction calls so assertions stay precise."""

        return [call for call in self.calls if call.role.value == role]


class StubLayaClient(LayaClient):
    """Returns canned reasoning plans and critiques, recording every request."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.plans: List[Dict[str, Any]] = []
        self.critiques: List[Dict[str, Any]] = []
        self.reason_calls: List[Any] = []
        self.critique_calls: List[Any] = []
        self.reason_error: Optional[Exception] = None
        self.critique_error: Optional[Exception] = None

    def queue_plan(self, *plans: Dict[str, Any]) -> "StubLayaClient":
        self.plans.extend(plans)
        return self

    def queue_critique(self, *critiques: Dict[str, Any]) -> "StubLayaClient":
        self.critiques.extend(critiques)
        return self

    async def reason(self, request: Any) -> Dict[str, Any]:
        self.reason_calls.append(request)
        if self.reason_error is not None:
            raise self.reason_error
        payload = dict(self.plans.pop(0)) if self.plans else default_plan(request.depth)
        payload["request_id"] = request.request_id
        payload.setdefault("depth", request.depth.value)
        return payload

    async def critique(self, request: Any) -> Dict[str, Any]:
        self.critique_calls.append(request)
        if self.critique_error is not None:
            raise self.critique_error
        payload = dict(self.critiques.pop(0)) if self.critiques else {"verdict": "approved"}
        payload["request_id"] = request.request_id
        payload.setdefault("summary", "Nessun problema rilevato.")
        return payload


def default_plan(depth: Any = "standard", **overrides: Any) -> Dict[str, Any]:
    depth_value = depth.value if hasattr(depth, "value") else str(depth)
    steps = [
        {"goal": "Identificare la richiesta", "rationale": "Serve il problema nudo.", "expected_output": "Oggetto"},
        {"goal": "Verificare i vincoli", "rationale": "Evita risposte non applicabili.", "expected_output": "Vincoli"},
    ]
    if depth_value == "standard":
        steps = steps[:1]
    payload = {
        "request_id": "stub",
        "steps": steps,
        "conclusion": "Risposta basata sui passi verificati.",
        "confidence": 0.82,
        "depth": depth_value,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def settings(tmp_path: Path, monkeypatch) -> Settings:
    # Tests must never read the developer's real .env or exported variables:
    # a suite that depends on local config passes here and fails on CI.
    for name in list(os.environ):
        if name.startswith(("LAYA_", "NEMOTRON_", "COREML_")):
            monkeypatch.delenv(name, raising=False)
    return Settings(
        _env_file=None,
        nemotron_api_key="test-key",
        nemotron_model="nvidia/nemotron-3.5-lightning-30b-a3b",
        laya_coreml_url="http://laya.invalid",
        laya_max_refinements=2,
        db_path=tmp_path / "laya.db",
    )


@pytest.fixture
def database(settings: Settings) -> Database:
    run(Database(settings.db_path).initialize())
    return Database(settings.db_path)


@pytest.fixture
def nemotron(settings: Settings) -> StubNemotronClient:
    return StubNemotronClient(settings)


@pytest.fixture
def laya(settings: Settings) -> StubLayaClient:
    return StubLayaClient(settings)


@pytest.fixture
def orchestrator(settings, database, nemotron, laya):
    nemotron_adapter = NemotronAdapter(settings, client=nemotron)
    laya_adapter = LayaAdapter(settings, client=laya)
    history = ChatHistoryManager(database)
    store = MemoryStore(database)
    memory = MemoryManager(store, nemotron_adapter)
    return HybridOrchestrator(
        settings=settings,
        nemotron=nemotron_adapter,
        laya=laya_adapter,
        history=history,
        memory=memory,
    )


def build_wired(settings, database, nemotron, laya):
    """Assemble a container whose components all use the stub runtimes."""

    nemotron_adapter = NemotronAdapter(settings, client=nemotron)
    container = Container(settings)
    container.database = database
    container.nemotron = nemotron_adapter
    container.laya = LayaAdapter(settings, client=laya)
    container.history = ChatHistoryManager(database)
    container.memory_store = MemoryStore(database)
    container.memory = MemoryManager(container.memory_store, nemotron_adapter)
    container.orchestrator = HybridOrchestrator(
        settings=settings,
        nemotron=nemotron_adapter,
        laya=container.laya,
        history=container.history,
        memory=container.memory,
    )
    return container


@pytest.fixture
def client(settings, database, nemotron, laya):
    from fastapi.testclient import TestClient

    return TestClient(create_app(build_wired(settings, database, nemotron, laya)))