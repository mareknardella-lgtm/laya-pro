"""Tests for the HTTP surface: chat, status, memory and error mapping."""

from __future__ import annotations


def test_chat_endpoint_returns_the_trace(client, nemotron):
    nemotron.queue("Ciao dal backend.")

    with client:
        response = client.post("/chat", json={"message": "Ciao", "tier": "LOW"})

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "Ciao dal backend."
    assert body["trace"][0]["stage"] == "nemotron.generate"


def test_chat_endpoint_rejects_a_blank_message(client):
    with client:
        response = client.post("/chat", json={"message": "   "})

    assert response.status_code == 422


def test_chat_endpoint_rejects_an_unknown_tier(client):
    with client:
        response = client.post("/chat", json={"message": "ciao", "tier": "TURBO"})

    assert response.status_code == 422


def test_unavailable_reasoning_tier_maps_to_503(client, laya):
    from backend.app.systems.laya.exceptions import LayaUnavailable

    laya.reason_error = LayaUnavailable("laya-coreml non raggiungibile")

    with client:
        response = client.post("/chat", json={"message": "Ciao", "tier": "MEDIUM"})

    assert response.status_code == 503
    assert response.json()["engine"] == "laya-coreml"


def test_status_reports_both_engines(client):
    with client:
        body = client.get("/status").json()

    assert body["engines"]["nemotron"]["configured"] is True
    assert body["engines"]["nemotron"]["role"] == "fast generation"
    assert body["engines"]["laya-coreml"]["role"] == "deep reasoning"
    assert body["tiers"]["HARD"].startswith("laya-coreml plan")


def test_status_is_honest_about_a_missing_api_key(client, settings):
    settings.nemotron_api_key = ""

    with client:
        body = client.get("/status").json()

    assert body["engines"]["nemotron"]["configured"] is False


def test_sessions_endpoint_lists_history(client, nemotron):
    nemotron.queue("Risposta 1.", "Risposta 2.")

    with client:
        client.post("/chat", json={"message": "A", "session_id": "s9", "tier": "LOW"})
        sessions = client.get("/sessions").json()
        messages = client.get("/sessions/s9/messages").json()

    assert sessions["count"] == 1
    assert messages["count"] == 2


def test_traces_endpoint_returns_the_pipeline(client, nemotron):
    nemotron.queue("Risposta tracciata.")

    with client:
        client.post("/chat", json={"message": "A", "session_id": "s8", "tier": "LOW"})
        traces = client.get("/sessions/s8/traces").json()

    assert traces["count"] == 1
    assert traces["traces"][0]["payload"]["trace"][0]["stage"] == "nemotron.generate"