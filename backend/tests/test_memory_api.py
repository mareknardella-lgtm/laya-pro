"""Tests for manual memory management: store CRUD and its HTTP endpoints."""

from __future__ import annotations

from backend.app.memory.models import MemoryEntry, MemoryScope
from backend.tests.conftest import run

# --------------------------------------------------------------- store CRUD


def test_store_roundtrips_an_entry(orchestrator):
    store = orchestrator._memory._store

    run(store.put(MemoryEntry(key="nome", value="Ada")))

    fetched = run(store.get("nome"))
    assert fetched is not None
    assert fetched.value == "Ada"
    assert fetched.scope is MemoryScope.PERSISTENT
    assert fetched.updated_at is not None


def test_store_get_returns_none_for_a_missing_key(orchestrator):
    assert run(orchestrator._memory._store.get("inesistente")) is None


def test_store_put_updates_an_existing_key(orchestrator):
    store = orchestrator._memory._store
    run(store.put(MemoryEntry(key="ruolo", value="junior")))
    run(store.put(MemoryEntry(key="ruolo", value="senior")))

    entries = run(store.all())
    assert len(entries) == 1
    assert entries[0].value == "senior"
    assert run(store.count()) == 1


def test_store_delete_removes_one_key_and_reports_whether_it_existed(orchestrator):
    store = orchestrator._memory._store
    run(store.put_many([MemoryEntry(key="a", value="1"), MemoryEntry(key="b", value="2")]))

    assert run(store.delete("a")) is True
    assert run(store.delete("a")) is False
    assert run(store.count()) == 1
    assert [entry.key for entry in run(store.all())] == ["b"]


def test_put_many_is_atomic_enough_for_the_batch(orchestrator):
    store = orchestrator._memory._store
    written = run(store.put_many([
        MemoryEntry(key="a", value="1"),
        MemoryEntry(key="b", value="2"),
    ]))
    assert written == 2
    assert run(store.count()) == 2


def test_remember_ignores_an_empty_batch(orchestrator):
    assert run(orchestrator._memory.remember([])) == 0


# ------------------------------------------------------------- http endpoints


def test_post_memory_creates_an_entry(client):
    with client:
        response = client.post("/memory", json={"key": "deploy", "value": "solo il venerdì"})

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["stored"]["key"] == "deploy"


def test_post_memory_replaces_a_silent_duplicate(client):
    """Posting the same key twice updates it instead of creating a second fact."""

    with client:
        client.post("/memory", json={"key": "deploy", "value": "primo"})
        second = client.post("/memory", json={"key": "deploy", "value": "secondo"})
        listing = client.get("/memory")

    assert second.json()["created"] is False
    assert listing.json()["count"] == 1
    assert listing.json()["entries"][0]["value"] == "secondo"


def test_put_memory_requires_an_existing_entry(client):
    with client:
        missing = client.put("/memory", json={"key": "inesistente", "value": "x"})
        client.post("/memory", json={"key": "esiste", "value": "prima"})
        edited = client.put("/memory", json={"key": "esiste", "value": "dopo"})

    assert missing.status_code == 404
    assert edited.status_code == 200
    assert edited.json()["stored"]["value"] == "dopo"


def test_delete_memory_roundtrip(client):
    with client:
        client.post("/memory", json={"key": "temporanea", "value": "x"})
        deleted = client.delete("/memory", params={"key": "temporanea"})
        again = client.delete("/memory", params={"key": "temporanea"})

    assert deleted.status_code == 200
    assert again.status_code == 404


def test_memory_endpoints_reject_empty_values(client):
    with client:
        blank = client.post("/memory", json={"key": "  ", "value": "x"})
        empty = client.post("/memory", json={"key": "k", "value": ""})

    assert blank.status_code == 422
    assert empty.status_code == 422


def test_query_endpoint_finds_a_manual_entry(client):
    with client:
        client.post("/memory", json={"key": "libreria", "value": "pandas per i dati"})
        client.post("/memory", json={"key": "porto", "value": "PostgreSQL"})

        hits = client.post("/memory/query", json={"query": "pandas dati"}).json()

    assert hits["count"] == 1
    assert hits["entries"][0]["key"] == "libreria"


def test_a_pinned_entry_reaches_the_prompt(client, nemotron):
    """The whole point of the feature: a pinned fact must reach the model even
    when the question shares no words with it."""

    with client:
        client.post("/memory", json={"key": "linguaggio", "value": "Rust"})
        nemotron.queue("Risposta con contesto.")
        client.post("/chat", json={"message": "Quale uso?", "tier": "LOW"})

    prompt = nemotron.calls[0].prompt
    assert "CONTESTO PERSONALE" in prompt
    assert "linguaggio: Rust" in prompt


def test_an_extracted_fact_still_needs_matching_words(orchestrator):
    """Lexical matching for extracted facts is intentional: they cannot all be
    injected into every prompt, or the context window fills with noise."""

    run(
        orchestrator._memory.remember(
            [MemoryEntry(key="colore preferito", value="blu", pinned=False)]
        )
    )

    unrelated, hits = run(orchestrator._memory.retrieve("prezzo del treno"))
    assert (unrelated, hits) == ("", 0)

    related, hits = run(orchestrator._memory.retrieve("qual è il mio colore"))
    assert hits == 1
    assert "blu" in related


def test_manually_added_entries_are_marked_pinned(client):
    with client:
        client.post("/memory", json={"key": "deploy", "value": "solo il venerdì"})
        listing = client.get("/memory").json()

    assert listing["entries"][0]["pinned"] is True


def test_store_put_never_unpins_an_existing_entry(orchestrator):
    """An extractor overwriting a fact must not silently drop the user's pin."""

    store = orchestrator._memory._store
    run(store.put(MemoryEntry(key="deploy", value="primo", pinned=True)))
    run(store.put(MemoryEntry(key="deploy", value="secondo", pinned=False)))

    entry = run(store.get("deploy"))
    assert entry.pinned is True
    assert entry.value == "secondo"