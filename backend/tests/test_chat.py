import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app

client = TestClient(create_app())


def test_get_modes():
    response = client.get("/api/v1/chat/modes")
    assert response.status_code == 200
    assert response.json() == {"modes": ["LOW", "MEDIUM", "HARD"]}


def test_post_message_unsupported_mode():
    response = client.post("/api/v1/chat/message", json={"session_id": "test_session", "message": "hello", "mode": "FAKE"})
    assert response.status_code == 422  # Pydantic validation fails


def test_post_message_low_mode(monkeypatch):
    # Mock System2Adapter to succeed without actual provider
    from backend.app.systems.system2.adapter import System2Adapter
    from backend.app.systems.system2.models import GenerationResponse

    async def mock_generate(*args, **kwargs):
        return GenerationResponse(request_id="test", text="Mocked response", engine="test_engine")
    
    monkeypatch.setattr(System2Adapter, "runtime_configured", property(lambda self: True))
    monkeypatch.setattr(System2Adapter, "generate", mock_generate)

    response = client.post("/api/v1/chat/message", json={"session_id": "test_session", "message": "hello", "mode": "LOW"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["text"] == "Mocked response"
    assert data["executed_mode"] == "LOW"
    assert "nemotron" in data["providers_used"]


def test_post_message_medium_mode_jev_unavailable():
    response = client.post("/api/v1/chat/message", json={"session_id": "test_session", "message": "hello", "mode": "MEDIUM"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["executed_mode"] == "NONE"
    assert "Motore JEV" in data["error_message"] or "motore JEV" in data["error_message"]


def test_post_message_hard_mode_jev_unavailable():
    response = client.post("/api/v1/chat/message", json={"session_id": "test_session", "message": "hello", "mode": "HARD"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["executed_mode"] == "NONE"
    assert "motore JEV" in data["error_message"] or "Motore JEV" in data["error_message"]


def test_post_message_medium_mode_jev_mocked(monkeypatch):
    from backend.app.chat.jev import JevAdapter
    from backend.app.chat.models import JevPlan
    from backend.app.systems.system2.adapter import System2Adapter
    from backend.app.systems.system2.models import GenerationResponse

    async def mock_analyze(*args, **kwargs):
        return JevPlan(intent="test", complexity="medium", strategy="test_strategy")

    async def mock_generate(*args, **kwargs):
        return GenerationResponse(request_id="test", text="Mocked response from plan", engine="test_engine")
    
    monkeypatch.setattr(JevAdapter, "analyze_and_plan", mock_analyze)
    monkeypatch.setattr(System2Adapter, "runtime_configured", property(lambda self: True))
    monkeypatch.setattr(System2Adapter, "generate", mock_generate)

    response = client.post("/api/v1/chat/message", json={"session_id": "test_session", "message": "hello", "mode": "MEDIUM"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["executed_mode"] == "MEDIUM"
    assert "jev" in data["providers_used"]
    assert "nemotron" in data["providers_used"]
    assert "Mocked response" in data["text"]


def test_post_message_hard_mode_jev_mocked(monkeypatch):
    from backend.app.chat.jev import JevAdapter
    from backend.app.chat.models import JevPlan, JevPlanStep
    from backend.app.systems.system2.adapter import System2Adapter
    from backend.app.systems.system2.models import GenerationResponse

    async def mock_analyze(*args, **kwargs):
        return JevPlan(
            intent="test", 
            complexity="high", 
            strategy="test_strategy",
            steps=[JevPlanStep(step_id="1", description="step 1")]
        )

    async def mock_generate(*args, **kwargs):
        return GenerationResponse(request_id="test", text="Mocked response hard mode", engine="test_engine")
    
    monkeypatch.setattr(JevAdapter, "analyze_and_plan", mock_analyze)
    monkeypatch.setattr(System2Adapter, "runtime_configured", property(lambda self: True))
    monkeypatch.setattr(System2Adapter, "generate", mock_generate)

    response = client.post("/api/v1/chat/message", json={"session_id": "test_session", "message": "hello", "mode": "HARD"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["executed_mode"] == "HARD"
    assert "jev" in data["providers_used"]
    assert "Mocked response" in data["text"]

def test_regression_create_session():
    response = client.post("/api/v1/chat/sessions", json={"title": "Regression Chat Session"})
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    
    # Verify the session is listed
    get_resp = client.get("/api/v1/chat/sessions")
    assert get_resp.status_code == 200
    sessions = get_resp.json()
    assert any(s["session_id"] == data["session_id"] for s in sessions)

def test_regression_create_session():
    response = client.post("/api/v1/chat/sessions", json={"title": "Regression Chat Session"})
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    
    # Verify the session is listed
    get_resp = client.get("/api/v1/chat/sessions")
    assert get_resp.status_code == 200
    sessions = get_resp.json()
    assert any(s["session_id"] == data["session_id"] for s in sessions)
