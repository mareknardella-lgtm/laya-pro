"""Composition root: builds every component once and shares it."""

from __future__ import annotations

from typing import Optional

from .chat.history import ChatHistoryManager
from .chat.orchestrator import HybridOrchestrator
from .config import Settings, get_settings
from .memory.manager import MemoryManager
from .memory.store import MemoryStore
from .storage.sqlite import Database
from .systems.laya.adapter import LayaAdapter
from .systems.nemotron.adapter import NemotronAdapter


class Container:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.database = Database(self.settings.db_path)
        self.nemotron = NemotronAdapter(self.settings)
        self.laya = LayaAdapter(self.settings)
        self.history = ChatHistoryManager(self.database)
        self.memory_store = MemoryStore(self.database)
        self.memory = MemoryManager(self.memory_store, self.nemotron)
        self.orchestrator = HybridOrchestrator(
            settings=self.settings,
            nemotron=self.nemotron,
            laya=self.laya,
            history=self.history,
            memory=self.memory,
        )

    async def startup(self) -> None:
        await self.database.initialize()

    async def shutdown(self) -> None:
        self.database.close()