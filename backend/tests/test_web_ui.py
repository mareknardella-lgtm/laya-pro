"""Tests for the web UI: static assets are served and the API keeps priority."""

from __future__ import annotations

from backend.app.static import STATIC_DIR


def test_index_is_served_at_root(client):
    with client:
        response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>Laya Pro</title>" in response.text


def test_static_assets_are_served(client):
    with client:
        css = client.get("/static/app.css")
        js = client.get("/static/app.js")
        favicon = client.get("/favicon.ico")

    assert css.status_code == 200
    assert "--accent" in css.text
    assert js.status_code == 200
    assert "renderMarkdown" in js.text
    assert favicon.status_code == 200


def test_static_files_are_validated_for_caching(client):
    """ETag/Last-Modified let the browser revalidate instead of refetching."""

    with client:
        response = client.get("/static/app.js")

    assert response.headers.get("etag")
    assert response.headers.get("last-modified")


def test_api_routes_are_not_shadowed_by_the_spa(client, nemotron):
    """A catch-all mount would silently swallow /status and /chat into HTML."""

    nemotron.queue("Risposta dallo stub.")

    with client:
        status = client.get("/status")
        chat = client.post("/chat", json={"message": "ciao", "tier": "LOW"})

    assert status.status_code == 200
    assert "engines" in status.json()
    assert "application/json" in status.headers["content-type"]

    assert chat.status_code == 200
    assert "application/json" in chat.headers["content-type"]
    assert chat.json()["text"] == "Risposta dallo stub."


def test_memory_panel_elements_exist_in_the_ui(client):
    """The web UI must expose the memory it writes to."""

    with client:
        html = client.get("/").text
        js = client.get("/static/app.js").text

    assert 'id="memory-drawer"' in html
    assert 'id="memory-form"' in html
    assert "/memory" in js
    assert "renderMemory" in js


def test_static_directory_contains_the_expected_assets():
    names = {path.name for path in STATIC_DIR.iterdir()}
    assert {"index.html", "app.css", "app.js"} <= names