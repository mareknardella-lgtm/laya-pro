"""Tests for memory extraction, retrieval and persistence."""

from __future__ import annotations

from backend.app.chat.models import ChatRequest, Tier
from backend.app.memory.models import MemoryEntry, MemoryScope
from backend.tests.conftest import run


def test_extraction_parses_key_value_pairs(orchestrator, nemotron):
    nemotron.queue("nome: Ada\nlingua: Python\nruolo: ingegnere")

    written = run(
        orchestrator._maybe_remember(
            ChatRequest(message="Chi sono?"), "Sono Ada."
        )
    )

    assert written == 3
    entries = run(orchestrator._memory._store.all(MemoryScope.PERSISTENT))
    keys = {entry.key for entry in entries}
    assert keys == {"nome", "lingua", "ruolo"}


def test_extraction_ignores_non_memory_output(orchestrator, nemotron):
    nemotron.queue("Nessun fatto permanente qui.")

    written = run(
        orchestrator._maybe_remember(ChatRequest(message="ciao"), "Ciao!")
    )

    assert written == 0


def test_retrieval_returns_only_relevant_entries(orchestrator, nemotron):
    run(
        orchestrator._memory.remember(
            [
                MemoryEntry(key="libreria preferita", value="pandas per l'analisi dati"),
                MemoryEntry(key="porto", value="server PostgreSQL in produzione"),
            ]
        )
    )

    block, hits = run(orchestrator._memory.retrieve("Quale libreria uso per i dati?"))

    assert hits == 1
    assert "pandas" in block
    assert "PostgreSQL" not in block


def test_retrieval_without_match_is_empty(orchestrator):
    run(orchestrator._memory.remember([MemoryEntry(key="a", value="colore preferito blu")]))

    block, hits = run(orchestrator._memory.retrieve("prezzo del biglietto del treno"))

    assert (block, hits) == ("", 0)


def test_memory_failure_never_blocks_a_chat(orchestrator, nemotron):
    """Memory is an enhancement: a broken write must not fail the answer."""

    nemotron.queue("Risposta utile.", "nome: Ada")

    async def explode(*args, **kwargs):
        raise RuntimeError("memoria rotta")

    orchestrator._memory.remember = explode  # type: ignore[assignment]

    response = run(
        orchestrator.process_message(ChatRequest(message="Ciao", tier=Tier.LOW))
    )

    assert response.status == "success"
    assert response.metadata["memory_written"] == 0


def test_conversation_is_persisted_and_replayed(orchestrator, nemotron):
    nemotron.queue("Prima risposta.", "Seconda risposta.")

    run(
        orchestrator.process_message(
            ChatRequest(message="Domanda 1", session_id="s1", auto_memory=False)
        )
    )
    run(
        orchestrator.process_message(
            ChatRequest(message="Domanda 2", session_id="s1", auto_memory=False)
        )
    )

    messages = run(orchestrator._history.get_messages("s1"))
    roles = [message["role"] for message in messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert messages[-1]["content"] == "Seconda risposta."


def test_message_direction_controls_which_end_you_get(orchestrator, nemotron):
    """The sidebar labels a chat with its opening message, not its last one."""

    nemotron.queue("Prima.", "Seconda.")
    run(
        orchestrator.process_message(
            ChatRequest(message="Apertura", session_id="s1", auto_memory=False)
        )
    )
    run(
        orchestrator.process_message(
            ChatRequest(message="Chiusura", session_id="s1", auto_memory=False)
        )
    )

    oldest = run(orchestrator._history.get_messages("s1", limit=1, direction="asc"))
    newest = run(orchestrator._history.get_messages("s1", limit=1, direction="desc"))

    assert oldest[0]["content"] == "Apertura"
    assert newest[0]["content"] == "Seconda."


def test_invalid_direction_is_rejected(orchestrator):
    import pytest

    with pytest.raises(ValueError):
        run(orchestrator._history.get_messages("s1", direction="sideways"))


def test_history_reaches_the_prompt_on_the_next_turn(orchestrator, nemotron):
    nemotron.queue("Prima.", "Seconda.")

    run(
        orchestrator.process_message(
            ChatRequest(message="A", session_id="s1", auto_memory=False)
        )
    )
    run(
        orchestrator.process_message(
            ChatRequest(message="B", session_id="s1", auto_memory=False)
        )
    )

    assert "A" in nemotron.calls[1].prompt
    assert "CRONOLOGIA RECENTE" in nemotron.calls[1].prompt