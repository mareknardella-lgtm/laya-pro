"""Long-term memory: extraction through the fast tier, retrieval by lexical scoring.

Retrieval deliberately avoids an extra model call: a deterministic keyword
score narrows the store to a handful of candidates, and only those candidates
are allowed into the prompt. This keeps the memory path off the critical
latency budget of the fast tier.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple

from ..systems.nemotron.adapter import NemotronAdapter
from ..systems.nemotron.models import RenderRole
from .models import MemoryEntry, MemoryScope
from .store import MemoryStore

_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)
_STOPWORDS = {
    "il", "lo", "la", "i", "gli", "le", "un", "una", "uno", "di", "a", "da", "in",
    "con", "su", "per", "tra", "fra", "e", "o", "ma", "che", "non", "sono", "sono",
    "the", "a", "an", "of", "to", "in", "on", "for", "with", "and", "or", "is",
    "are", "my", "your", "i", "you", "we", "it", "this", "that",
}


def tokenize(text: str) -> List[str]:
    return [
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if len(token) > 2 and token.lower() not in _STOPWORDS
    ]


class MemoryManager:
    def __init__(
        self,
        store: MemoryStore,
        nemotron: NemotronAdapter,
        max_context_entries: int = 8,
        max_pinned_entries: int = 4,
    ) -> None:
        self._store = store
        self._nemotron = nemotron
        self._max_context_entries = max_context_entries
        self._max_pinned_entries = max_pinned_entries

    async def extract(self, turns: Sequence[dict]) -> List[MemoryEntry]:
        """Ask the fast tier for durable facts. Failures are non-fatal."""

        transcript = "\n".join(
            f"{turn.get('role', 'user')}: {turn.get('content', '')}" for turn in turns
        ).strip()
        if not transcript:
            return []

        try:
            text = await self._nemotron.render(
                prompt=transcript, role=RenderRole.EXTRACT
            )
        except Exception:
            return []

        return self._store.parse_extraction(text)

    async def remember(self, entries: Iterable[MemoryEntry]) -> int:
        materialized = list(entries)
        if not materialized:
            return 0
        return await self._store.put_many(materialized)

    async def retrieve(self, query: str, history: Sequence[dict] = ()) -> Tuple[str, int]:
        """Return (context block, number of entries used) for the given query.

        Pinned facts are always included: the user added them on purpose, so
        they are relevant regardless of how the question is worded. Extracted
        facts still have to match the question's vocabulary, otherwise every
        chat would drag along the entire memory store.
        """

        entries = await self._store.all(MemoryScope.PERSISTENT)
        if not entries:
            return "", 0

        pinned = [entry for entry in entries if entry.pinned][: self._max_pinned_entries]
        pinned_keys = {entry.key for entry in pinned}

        haystack_tokens = set(tokenize(query))
        for turn in history[-5:]:
            haystack_tokens.update(tokenize(str(turn.get("content", ""))))

        scored = [
            (entry, _score(entry, haystack_tokens))
            for entry in entries
            if entry.key not in pinned_keys
        ]
        matches = [
            entry for entry, score in sorted(scored, key=lambda pair: pair[1], reverse=True)
            if score > 0
        ]
        room = max(0, self._max_context_entries - len(pinned))
        selected = pinned + matches[:room]
        if not selected:
            return "", 0

        block = "\n".join(f"- {entry.key}: {entry.value}" for entry in selected)
        return block, len(selected)

    async def query(self, query: str, limit: int = 10) -> List[MemoryEntry]:
        """Direct RAG lookup used by the /memory/query endpoint."""

        entries = await self._store.all(MemoryScope.PERSISTENT)
        tokens = set(tokenize(query))
        scored = [(entry, _score(entry, tokens)) for entry in entries]
        hits = [entry for entry, score in sorted(scored, key=lambda pair: pair[1], reverse=True) if score > 0]
        return hits[:limit]


def _score(entry: MemoryEntry, tokens: Iterable[str]) -> float:
    token_set = set(tokens)
    if not token_set:
        return 0.0
    entry_tokens = set(tokenize(f"{entry.key} {entry.value}"))
    if not entry_tokens:
        return 0.0
    overlap = len(entry_tokens & token_set)
    if overlap == 0:
        return 0.0
    return overlap / (len(token_set) ** 0.5)