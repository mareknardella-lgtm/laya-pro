"""Service health probing, critical-workflow classification and safety audit recording."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app

SERVICES_PATH = "/api/v1/health/services"


def _settings(tmp_path: Path) -> Settings:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    other = tmp_path / "other-project"
    other.mkdir(exist_ok=True)
    return Settings(
        database_path=tmp_path / "safety.sqlite3",
        project_roots={"demo": project, "other": other},
        allowed_permissions=frozenset({"project:read", "project:write"}),
        approval_token="operator-token-for-local-tests-only",
    )


def _headers() -> dict[str, str]:
    return {"X-Laya-Approval-Token": "operator-token-for-local-tests-only"}


def _fake_system1(monkeypatch: pytest.MonkeyPatch, behaviour) -> None:
    """Replaces the System 1 probe so the test never depends on a live daemon."""

    async def fake_get(self, url, *args, **kwargs):  # noqa: ANN001 - mirrors httpx signature
        if isinstance(behaviour, Exception):
            raise behaviour
        return httpx.Response(200 if behaviour == "online" else 503, json={"status": behaviour}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


def test_services_health_reports_both_services_operational(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_system1(monkeypatch, "online")
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get(SERVICES_PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["overall_status"] == "all_operational"
    assert body["backend"]["name"] == "Backend FastAPI"
    assert body["backend"]["status"] == "online"
    assert body["backend"]["latency_ms"] is not None
    assert body["system1"]["name"] == "Laya System 1"
    assert body["system1"]["status"] == "online"
    assert body["system1"]["endpoint"].startswith("http://127.0.0.1:8000")
    assert body["system1"]["checked_at"]


@pytest.mark.parametrize(
    ("behaviour", "expected_status", "expected_message"),
    [
        (httpx.ConnectError("connection refused"), "offline", "connessione rifiutata"),
        (httpx.ReadTimeout("timed out"), "error", "Timeout"),
        (httpx.HTTPError("unexpected failure"), "error", "Errore verifica System 1"),
    ],
)
def test_services_health_distinguishes_system1_failure_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    behaviour: Exception,
    expected_status: str,
    expected_message: str,
) -> None:
    _fake_system1(monkeypatch, behaviour)
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get(SERVICES_PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["system1"]["status"] == expected_status
    assert expected_message in body["system1"]["message"]
    # The backend itself is always reachable here, so the aggregate state is "partial".
    assert body["overall_status"] == "partial"
    assert body["backend"]["status"] == "online"


def test_services_health_reports_an_http_error_from_system1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_system1(monkeypatch, "unhealthy")
    with TestClient(create_app(_settings(tmp_path))) as client:
        body = client.get(SERVICES_PATH).json()

    assert body["system1"]["status"] == "error"
    assert "503" in body["system1"]["message"]
    assert body["overall_status"] == "partial"


def test_critical_alert_event_requires_authorization_and_records_an_audit_event(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    payload = {
        "event_type": "system1_failure_interruption",
        "workflow_id": "workflow-1",
        "execution_id": "execution-1",
        "project_id": "demo",
        "status": "paused_safely",
        "details": {"system1_status": "offline", "consecutive_failures": 2, "stop_confirmed": True},
    }

    with TestClient(app) as client:
        assert client.post("/api/v1/operator/critical-alert/event", json=payload).status_code == 401
        rejected = client.post("/api/v1/operator/critical-alert/event", headers=_headers(), json={**payload, "operator_token": "leaked"})
        assert rejected.status_code == 422

        recorded = client.post("/api/v1/operator/critical-alert/event", headers=_headers(), json=payload)
        assert recorded.status_code == 200
        assert recorded.json() == {"status": "recorded"}

        scoped = client.get("/api/v1/audit?project_id=demo", headers=_headers())
        assert scoped.status_code == 200
        events = [row for row in scoped.json() if row["event_type"] == "critical_safety.system1_failure_interruption"]
        assert len(events) == 1
        event = events[0]
        assert event["actor_id"] == "operator:safety-monitor"
        assert event["status"] == "paused_safely"
        assert event["workflow_id"] == "workflow-1"
        assert event["execution_id"] == "execution-1"
        assert event["details"]["system1_status"] == "offline"
        assert event["details"]["consecutive_failures"] == 2
        assert event["created_at"]

        other_scope = client.get("/api/v1/audit?project_id=other", headers=_headers())
        assert all(row["event_type"] != "critical_safety.system1_failure_interruption" for row in other_scope.json())

        # Credentials must never be persisted in safety audit records.
        secret = client.post(
            "/api/v1/operator/critical-alert/event",
            headers=_headers(),
            json={**payload, "event_type": "operator_acknowledged", "details": {"approval_token": "operator-token-for-local-tests-only"}},
        )
        assert secret.status_code == 200
        all_events = client.get("/api/v1/audit?limit=200", headers=_headers())
        assert "operator-token-for-local-tests-only" not in all_events.text


def test_workflow_summaries_flag_only_approval_requiring_workflows_as_critical(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    (app.state.settings.project_roots["demo"] / "hello.txt").write_text("hello", encoding="utf-8")

    with TestClient(app) as client:
        for path, content in (("replaced.txt", "new content"), ("written.txt", "other content")):
            assert client.post("/api/v1/workflows", headers=_headers(), json={
                "run_immediately": True,
                "definition": {
                    "project_id": "demo",
                    "steps": [{"step_id": "write", "tool_id": "file.replace", "parameters": {"path": path, "content": content, "expected_sha256": None}}],
                },
            }).status_code == 201
        assert client.post("/api/v1/workflows", headers=_headers(), json={
            "run_immediately": True,
            "definition": {
                "project_id": "demo",
                "steps": [{"step_id": "read", "tool_id": "file.read", "parameters": {"path": "hello.txt"}}],
            },
        }).status_code == 201

        summaries = client.get("/api/v1/operator/workflows?limit=50", headers=_headers())
        assert summaries.status_code == 200
        critical = {row["workflow_id"]: row["is_critical"] for row in summaries.json() if any(step["tool_id"] == "file.replace" for step in row["steps"])}
        read_only = {row["workflow_id"]: row["is_critical"] for row in summaries.json() if all(step["tool_id"] == "file.read" for step in row["steps"])}

        assert len(critical) == 2
        assert all(critical.values()), "high-risk tools make a workflow critical"
        assert len(read_only) == 1
        assert not any(read_only.values()), "a low-risk read-only workflow is not critical"


def test_interrupted_workflow_can_be_paused_and_is_visible_as_a_critical_record(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        created = client.post("/api/v1/workflows", headers=_headers(), json={
            "run_immediately": True,
            "definition": {
                "project_id": "demo",
                "steps": [{"step_id": "write", "tool_id": "file.replace", "parameters": {"path": "notes.txt", "content": "data", "expected_sha256": None}}],
            },
        })
        workflow_id = created.json()["workflow_id"]

        paused = client.post(f"/api/v1/operator/workflows/{workflow_id}/pause", headers=_headers())
        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"
        assert paused.json()["is_critical"] is True
        assert paused.json()["pending_step"] == "write"

        resumed = client.post(f"/api/v1/operator/workflows/{workflow_id}/resume", headers=_headers())
        assert resumed.status_code == 409, "a pending approval still blocks an unsafe resume"
