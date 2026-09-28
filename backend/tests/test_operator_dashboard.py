from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.observability.audit import AuditEvent
from backend.app.observability.executions import ExecutionHistoryStatus
from backend.app.workflows.models import WorkflowDefinition


def _settings(tmp_path: Path, *, permissions: frozenset[str] = frozenset({"project:read", "project:write"})) -> Settings:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    other = tmp_path / "other-project"
    other.mkdir(exist_ok=True)
    return Settings(
        database_path=tmp_path / "operator.sqlite3",
        project_roots={"demo": project, "other": other},
        allowed_permissions=permissions,
        approval_token="operator-token-for-local-tests-only",
    )


def _headers() -> dict[str, str]:
    return {"X-Laya-Approval-Token": "operator-token-for-local-tests-only"}


def test_dashboard_is_mounted_with_same_origin_security_headers(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        root = client.get("/", follow_redirects=False)
        assert root.status_code == 307
        assert root.headers["location"] == "/dashboard/"

        page = client.get("/dashboard/")
        assert page.status_code == 200
        assert "Laya Pro" in page.text
        assert "Benvenuto in Laya Pro" in page.text
        assert "/dashboard/app.js" in page.text
        assert "Content-Security-Policy" in page.headers
        assert page.headers["X-Content-Type-Options"] == "nosniff"

        script = client.get("/dashboard/app.js")
        stylesheet = client.get("/dashboard/styles.css")
        assert script.status_code == 200
        assert "operator/approvals" in script.text
        assert stylesheet.status_code == 200


def test_operator_contracts_require_token_and_expose_only_configured_projects(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    token = _headers()
    with TestClient(app) as client:
        for endpoint in (
            "/api/v1/operator/status",
            "/api/v1/operator/projects",
            "/api/v1/operator/approvals",
            "/api/v1/operator/workflows",
            "/api/v1/operator/executions",
        ):
            assert client.get(endpoint).status_code == 401

        assert client.get("/health").status_code == 200
        status = client.get("/api/v1/operator/status", headers=token)
        assert status.status_code == 200
        assert status.json()["api_status"] == "healthy"
        assert status.json()["system1"]["status"] == "unavailable"
        assert status.json()["system2"]["status"] == "unavailable"
        assert status.json()["system2"]["native_runtime_verified"] is False
        projects = client.get("/api/v1/operator/projects", headers=token)
        assert projects.status_code == 200
        assert projects.json() == ["demo", "other"]
        assert str(settings.project_roots["demo"]) not in projects.text
        assert client.get("/api/v1/operator/status?project_id=not-configured", headers=token).status_code == 200


def test_project_filters_scope_operator_lists_details_mutations_status_and_audit(tmp_path: Path) -> None:
    roots = {project_id: tmp_path / project_id for project_id in ("demo", "other")}
    for root in roots.values():
        root.mkdir()
        (root / "hello.txt").write_text("project-local", encoding="utf-8")
    settings = Settings(
        database_path=tmp_path / "projects.sqlite3",
        project_roots=roots,
        allowed_permissions=frozenset({"project:read", "project:write"}),
        approval_token="operator-token-for-local-tests-only",
    )
    app = create_app(settings)
    headers = _headers()
    workflows: dict[str, str] = {}
    approvals: dict[str, str] = {}
    executions: dict[str, str] = {}

    with TestClient(app) as client:
        assert client.get("/api/v1/operator/projects").status_code == 401
        assert client.get("/api/v1/operator/status?project_id=not-configured", headers=headers).status_code == 200
        listed_projects = client.get("/api/v1/operator/projects", headers=headers)
        assert listed_projects.status_code == 200
        assert listed_projects.json() == ["demo", "other"]
        assert str(tmp_path) not in listed_projects.text

        for project_id in roots:
            created = client.post("/api/v1/workflows", headers=headers, json={
                "run_immediately": True,
                "definition": {
                    "project_id": project_id,
                    "steps": [{
                        "step_id": "write",
                        "tool_id": "file.replace",
                        "parameters": {"path": "pending.txt", "content": "private content", "expected_sha256": None},
                    }],
                },
            })
            assert created.status_code == 201
            workflows[project_id] = created.json()["workflow_id"]
            approvals[project_id] = created.json()["state"]["approval_requests"]["write"]

            read = client.post("/api/v1/files/read", headers=headers, json={
                "project_id": project_id,
                "path": "hello.txt",
                "idempotency_key": f"read-{project_id}",
            })
            assert read.status_code == 200
            executions[project_id] = read.json()["execution_id"]

        for decision_id, project_id in (("decision-demo", "demo"), ("decision-other", "other"), ("decision-hidden", "removed")):
            with app.state.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO decision_events (
                        decision_id, request_id, workflow_id, project_id, created_at, status, kind, action_id, execution_status
                    ) VALUES (?, ?, NULL, ?, ?, ?, ?, NULL, ?)""",
                    (decision_id, f"request-{decision_id}", project_id, datetime.now(timezone.utc).isoformat(), "completed", "NO_ACTION", "not_run"),
                )
            if project_id != "removed":
                app.state.audit_log.record(AuditEvent(
                    actor_id="test",
                    event_type="decision.recorded",
                    project_id=project_id,
                    decision_id=decision_id,
                    status="completed",
                ))

        all_workflows = client.get("/api/v1/operator/workflows", headers=headers)
        demo_workflows = client.get("/api/v1/operator/workflows?project_id=demo", headers=headers)
        unknown_workflows = client.get("/api/v1/operator/workflows?project_id=not-configured", headers=headers)
        assert all_workflows.status_code == 200 and len(all_workflows.json()) == 2
        assert demo_workflows.status_code == 200
        assert {record["project_id"] for record in demo_workflows.json()} == {"demo"}
        assert unknown_workflows.status_code == 200 and unknown_workflows.json() == []

        all_approvals = client.get("/api/v1/operator/approvals", headers=headers)
        demo_approvals = client.get("/api/v1/operator/approvals?project_id=demo", headers=headers)
        assert all_approvals.status_code == 200 and len(all_approvals.json()) == 2
        assert [row["approval"]["project_id"] for row in demo_approvals.json()] == ["demo"]
        assert client.get(f"/api/v1/operator/workflows/{workflows['demo']}?project_id=other", headers=headers).status_code == 404
        assert client.get(f"/api/v1/operator/approvals/{approvals['demo']}?project_id=other", headers=headers).status_code == 404
        wrong_project_decision = client.post(
            f"/api/v1/operator/approvals/{approvals['demo']}/approve?project_id=other",
            headers=headers,
        )
        assert wrong_project_decision.status_code == 404
        assert app.state.approval_gate.get(approvals["demo"]).status.value == "pending"
        other_scope_rejected = client.post(
            f"/api/v1/operator/workflows/{workflows['demo']}/pause?project_id=other",
            headers=headers,
        )
        assert other_scope_rejected.status_code == 404
        assert app.state.workflow_engine.get(workflows["demo"]).status.value == "awaiting_approval"
        scoped_transition = client.post(
            f"/api/v1/operator/workflows/{workflows['demo']}/pause?project_id=demo",
            headers=headers,
        )
        assert scoped_transition.status_code == 200
        assert scoped_transition.json()["status"] == "paused"

        all_executions = client.get("/api/v1/operator/executions", headers=headers)
        demo_executions = client.get("/api/v1/operator/executions?project_id=demo", headers=headers)
        assert all_executions.status_code == 200 and all_executions.json()["total"] == 2
        assert demo_executions.status_code == 200
        assert {row["project_id"] for row in demo_executions.json()["items"]} == {"demo"}
        assert client.get(f"/api/v1/operator/executions/{executions['demo']}?project_id=other", headers=headers).status_code == 404

        demo_decisions = client.get("/api/v1/operator/decisions?project_id=demo", headers=headers)
        unknown_decisions = client.get("/api/v1/operator/decisions?project_id=not-configured", headers=headers)
        assert demo_decisions.status_code == 200
        assert [row["decision_id"] for row in demo_decisions.json()["items"]] == ["decision-demo"]
        assert unknown_decisions.status_code == 200 and unknown_decisions.json()["total"] == 0

        demo_status = client.get("/api/v1/operator/status?project_id=demo", headers=headers)
        unknown_status = client.get("/api/v1/operator/status?project_id=not-configured", headers=headers)
        assert demo_status.status_code == 200
        assert demo_status.json()["active_workflow_count"] == 1
        assert demo_status.json()["pending_approval_count"] == 1
        assert unknown_status.status_code == 200
        assert unknown_status.json()["active_workflow_count"] == 0
        assert unknown_status.json()["pending_approval_count"] == 0
        assert unknown_status.json()["execution_counts"] == {}

        with app.state.database.transaction() as connection:
            connection.execute(
                "INSERT INTO audit_events (event_id, created_at, actor_id, event_type, request_id, workflow_id, decision_id, execution_id, resource_id, status, details_json) VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?)",
                ("removed-project-audit", datetime.now(timezone.utc).isoformat(), "test", "test.event", "removed:key", "ok", json.dumps({"key": "value"})),
            )
            connection.execute(
                "INSERT INTO audit_events (event_id, created_at, actor_id, event_type, request_id, workflow_id, decision_id, execution_id, resource_id, status, details_json) VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?)",
                ("removed-project-details-audit", datetime.now(timezone.utc).isoformat(), "test", "test.event", "unrelated", "ok", json.dumps({"project_id": "removed"})),
            )
            connection.execute(
                "INSERT INTO audit_events (event_id, created_at, actor_id, event_type, request_id, workflow_id, decision_id, execution_id, resource_id, status, details_json) VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?)",
                ("legacy-workflow-event", datetime.now(timezone.utc).isoformat(), "test", "workflow.started", workflows["demo"], "ok", json.dumps({"version": 1})),
            )
        all_audit = client.get("/api/v1/audit", headers=headers)
        demo_audit = client.get("/api/v1/audit?project_id=demo", headers=headers)
        unknown_audit = client.get("/api/v1/audit?project_id=not-configured", headers=headers)
        assert all_audit.status_code == demo_audit.status_code == unknown_audit.status_code == 200
        assert any(row["workflow_id"] == workflows["demo"] for row in demo_audit.json())
        assert any(row["event_id"] == "legacy-workflow-event" for row in demo_audit.json())
        assert any(row["decision_id"] == "decision-demo" for row in demo_audit.json())
        assert all(row["workflow_id"] != workflows["other"] for row in demo_audit.json())
        assert all(row["event_id"] != "removed-project-audit" for row in all_audit.json())
        assert all(row["event_id"] != "removed-project-details-audit" for row in all_audit.json())
        assert unknown_audit.json() == []


def test_workflow_approval_preview_and_lifecycle_use_backend_fingerprint(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    headers = _headers()
    secret_like_content = "private-file-content-that-must-not-appear-in-operator-response"
    parameters = {"path": "notes.txt", "content": secret_like_content, "expected_sha256": None}

    with TestClient(app) as client:
        created = client.post("/api/v1/workflows", headers=headers, json={
            "run_immediately": True,
            "definition": {
                "project_id": "demo",
                "steps": [{"step_id": "write", "tool_id": "file.replace", "parameters": parameters}],
            },
        })
        assert created.status_code == 201
        workflow_id = created.json()["workflow_id"]
        approval_id = created.json()["state"]["approval_requests"]["write"]
        assert created.json()["status"] == "awaiting_approval"

        denied = client.get(f"/api/v1/operator/approvals/{approval_id}")
        assert denied.status_code == 401

        approval = client.get(f"/api/v1/operator/approvals/{approval_id}", headers=headers)
        assert approval.status_code == 200
        payload = approval.json()
        assert payload["approval"]["workflow_id"] == workflow_id
        assert payload["approval"]["project_id"] == "demo"
        assert payload["validation"]["status"] == "valid"
        assert payload["parameters_preview"]["path"] == "notes.txt"
        assert payload["parameters_preview"]["content"].startswith("[OMITTED")
        assert secret_like_content not in approval.text
        assert payload["approval"]["expires_at"]

        approvals = client.get("/api/v1/operator/approvals", headers=headers)
        assert approvals.status_code == 200
        assert len(approvals.json()) == 1

        rejected_unauthorized = client.post(f"/api/v1/operator/approvals/{approval_id}/approve")
        assert rejected_unauthorized.status_code == 401
        decision = client.post(f"/api/v1/operator/approvals/{approval_id}/approve", headers=headers)
        assert decision.status_code == 200
        assert decision.json()["status"] == "approved"
        assert decision.json()["decided_by"] == "local-operator"
        assert secret_like_content not in decision.text

        operator_detail = client.get(f"/api/v1/operator/workflows/{workflow_id}", headers=headers)
        assert operator_detail.status_code == 200
        assert operator_detail.json()["steps"][0]["approval_status"] == "approved"
        assert operator_detail.json()["steps"][0]["approval_expires_at"]

        current_step_approval = {"write": approval_id}
        wrong_step_mapping = client.post(
            f"/api/v1/operator/workflows/{workflow_id}/resume",
            headers=headers,
            json={"approval_ids": {"write": approval_id, "other": approval_id}},
        )
        assert wrong_step_mapping.status_code == 409

        paused = client.post(f"/api/v1/operator/workflows/{workflow_id}/pause", headers=headers)
        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"
        resumed = client.post(f"/api/v1/operator/workflows/{workflow_id}/resume", headers=headers, json={"approval_ids": current_step_approval})
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "completed"
        assert (settings.project_roots["demo"] / "notes.txt").read_text(encoding="utf-8") == secret_like_content

        # Approval remains an exact, single-use grant; resuming the completed workflow does not replay it.
        conflict = client.post(f"/api/v1/operator/approvals/{approval_id}/approve", headers=headers)
        assert conflict.status_code == 409


def test_execution_history_filters_details_and_hides_other_project_records(tmp_path: Path) -> None:
    settings = _settings(tmp_path, permissions=frozenset({"project:read"}))
    (settings.project_roots["demo"] / "hello.txt").write_text("hello", encoding="utf-8")
    app = create_app(settings)
    headers = _headers()

    with TestClient(app) as client:
        result = client.post("/api/v1/files/read", headers=headers, json={
            "project_id": "demo",
            "path": "hello.txt",
            "idempotency_key": "dashboard-read-1",
        })
        assert result.status_code == 200
        execution_id = result.json()["execution_id"]

        history = client.get(f"/api/v1/operator/executions?status=succeeded&q={execution_id}&limit=20&offset=0", headers=headers)
        assert history.status_code == 200
        assert history.json()["total"] == 1
        record = history.json()["items"][0]
        assert record["execution_id"] == execution_id
        assert record["workflow_id"] is None
        assert record["project_id"] == "demo"
        assert record["duration_ms"] >= 0

        detail = client.get(f"/api/v1/operator/executions/{execution_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["execution"]["execution_id"] == execution_id
        assert "hello" not in detail.text
        assert "idempotency_key" not in detail.json()["execution"]

        missing_workflow_execution = "missing-workflow-execution"
        app.state.execution_history.start(
            missing_workflow_execution,
            "missing-workflow-idempotency",
            "missing-workflow-request",
            "demo",
            "deleted-workflow",
            "step-1",
            "file.read",
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        app.state.execution_history.finish(
            missing_workflow_execution,
            ExecutionHistoryStatus.SUCCEEDED,
            datetime.now(timezone.utc),
            1,
        )
        app.state.audit_log.record(AuditEvent(
            actor_id="test",
            event_type="tool.execution.completed",
            project_id="demo",
            workflow_id="deleted-workflow",
            execution_id=missing_workflow_execution,
            status="succeeded",
        ))
        missing_workflow_detail = client.get(
            f"/api/v1/operator/executions/{missing_workflow_execution}",
            headers=headers,
        )
        assert missing_workflow_detail.status_code == 200
        assert missing_workflow_detail.json()["workflow"] is None
        assert [event["event_type"] for event in missing_workflow_detail.json()["events"]] == ["tool.execution.completed"]

        rogue_id = "rogue-execution"
        app.state.execution_history.start(
            rogue_id,
            "internal-idempotency-key",
            "request-rogue",
            "other-project",
            None,
            None,
            "file.read",
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        app.state.execution_history.finish(
            rogue_id,
            ExecutionHistoryStatus.SUCCEEDED,
            datetime.now(timezone.utc),
            1,
        )
        scoped = client.get("/api/v1/operator/executions?limit=20&offset=0", headers=headers)
        assert scoped.status_code == 200
        assert all(item["execution_id"] != rogue_id for item in scoped.json()["items"])
        assert client.get("/api/v1/operator/executions?project_id=other-project", headers=headers).json()["total"] == 0
        denied_detail = client.get(f"/api/v1/operator/executions/{rogue_id}", headers=headers)
        assert denied_detail.status_code == 404

        cross_project_execution = "cross-project-workflow-execution"
        app.state.execution_history.start(
            cross_project_execution,
            "cross-project-workflow-idempotency",
            "cross-project-workflow-request",
            "demo",
            "other-workflow-id",
            "step-1",
            "file.read",
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        app.state.execution_history.finish(
            cross_project_execution,
            ExecutionHistoryStatus.SUCCEEDED,
            datetime.now(timezone.utc),
            1,
        )
        app.state.workflow_store.create(
            "other-workflow-id",
            WorkflowDefinition(
                project_id="other",
                steps=[{"step_id": "read", "tool_id": "file.read", "parameters": {"path": "hello.txt"}}],
            ),
        )
        app.state.audit_log.record(AuditEvent(
            actor_id="test",
            event_type="tool.execution.completed",
            project_id="demo",
            workflow_id="other-workflow-id",
            execution_id=cross_project_execution,
            resource_id="other-workflow-id",
            status="succeeded",
        ))
        cross_project_detail = client.get(
            f"/api/v1/operator/executions/{cross_project_execution}",
            headers=headers,
        )
        assert cross_project_detail.status_code == 200
        assert cross_project_detail.json()["workflow"] is None
        assert len(cross_project_detail.json()["events"]) == 1
        assert cross_project_detail.json()["events"][0]["workflow_id"] is None
        assert cross_project_detail.json()["events"][0]["resource_id"] is None
        assert cross_project_detail.json()["execution"]["workflow_id"] == "other-workflow-id"
        assert "other-workflow-id" in cross_project_detail.text


def test_expired_workflow_approval_is_reported_and_cannot_be_approved(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    headers = _headers()
    parameters = {"path": "notes.txt", "content": "data", "expected_sha256": None}
    with TestClient(app) as client:
        created = client.post("/api/v1/workflows", headers=headers, json={
            "run_immediately": True,
            "definition": {"project_id": "demo", "steps": [{"step_id": "write", "tool_id": "file.replace", "parameters": parameters}]},
        })
        approval_id = created.json()["state"]["approval_requests"]["write"]
        with app.state.database.transaction() as connection:
            connection.execute(
                "UPDATE approvals SET expires_at = ? WHERE approval_id = ?",
                ((datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(), approval_id),
            )
        detail = client.get(f"/api/v1/operator/approvals/{approval_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["approval"]["status"] == "expired"
        assert detail.json()["validation"]["status"] == "expired"
        approved = client.post(f"/api/v1/operator/approvals/{approval_id}/approve", headers=headers)
        assert approved.status_code == 409
        assert approved.json()["detail"]["code"] == "approval_conflict"


def test_orphaned_workflow_approval_is_visible_as_unavailable_but_cannot_be_decided(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    headers = _headers()
    secret_like_content = "never-return-this-content"
    with TestClient(app) as client:
        created = client.post("/api/v1/workflows", headers=headers, json={
            "run_immediately": True,
            "definition": {
                "project_id": "demo",
                "steps": [{
                    "step_id": "write",
                    "tool_id": "file.replace",
                    "parameters": {"path": "notes.txt", "content": secret_like_content, "expected_sha256": None},
                }],
            },
        })
        assert created.status_code == 201
        approval_id = created.json()["state"]["approval_requests"]["write"]
        workflow_id = created.json()["workflow_id"]

        with app.state.database.transaction() as connection:
            row = connection.execute("SELECT state_json FROM workflow_records WHERE workflow_id = ?", (workflow_id,)).fetchone()
            state = json.loads(row["state_json"])
            state["approval_requests"] = {}
            connection.execute(
                "UPDATE workflow_records SET state_json = ? WHERE workflow_id = ?",
                (json.dumps(state), workflow_id),
            )

        listed = client.get("/api/v1/operator/approvals", headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert listed.json()[0]["approval"]["approval_id"] == approval_id
        assert listed.json()[0]["validation"]["status"] == "invalid"
        assert secret_like_content not in listed.text

        detail = client.get(f"/api/v1/operator/approvals/{approval_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["validation"]["status"] == "invalid"
        assert secret_like_content not in detail.text
        for decision in ("approve", "reject"):
            response = client.post(f"/api/v1/operator/approvals/{approval_id}/{decision}", headers=headers)
            assert response.status_code == 409


def test_operator_decisions_are_scoped_to_configured_projects(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    now = datetime.now(timezone.utc).isoformat()
    with app.state.database.transaction() as connection:
        for decision_id, project_id in (("visible-decision", "demo"), ("hidden-decision", "other-project")):
            connection.execute(
                """INSERT INTO decision_events (
                    decision_id, request_id, workflow_id, project_id, created_at, status, kind, action_id, execution_status
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, NULL, ?)""",
                (decision_id, f"request-{decision_id}", project_id, now, "completed", "NO_ACTION", "not_run"),
            )

    with TestClient(app) as client:
        response = client.get("/api/v1/operator/decisions", headers=_headers())
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["decision_id"] for item in response.json()["items"]] == ["visible-decision"]
    assert "hidden-decision" not in response.text


def test_execution_history_redacts_credential_assignments(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    now = datetime.now(timezone.utc)
    app.state.execution_history.start("safe-exec", "safe-idem", "safe-request", "demo", None, None, "file.read", now)
    app.state.execution_history.finish(
        "safe-exec",
        ExecutionHistoryStatus.FAILED,
        now + timedelta(milliseconds=4),
        4,
        error_code="failed",
        error_summary="tool failed; password=hunter2; bearer sk-example-not-a-real-token",
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/operator/executions/safe-exec", headers=_headers())
    assert response.status_code == 200
    assert "hunter2" not in response.text

    assert "sk-example-not-a-real-token" not in response.text
    assert "[REDACTED]" in response.text


def test_operator_session_connect_disconnect_and_cross_origin_protections(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        # 1. Block cross-origin requests
        cross_origin = client.post(
            "/api/v1/operator/session/connect",
            headers={"Origin": "https://malicious-website.com"},
        )
        assert cross_origin.status_code == 403
        assert cross_origin.json()["detail"]["code"] == "forbidden_origin"

        # 2. Block cross-site fetch
        cross_site = client.post(
            "/api/v1/operator/session/connect",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert cross_site.status_code == 403
        assert cross_site.json()["detail"]["code"] == "cross_site_blocked"

        # 3. Successful same-origin loopback session connection
        connect = client.post(
            "/api/v1/operator/session/connect",
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert connect.status_code == 200
        session_data = connect.json()
        assert session_data["status"] == "connected"
        assert session_data["session_token"].startswith("laya_sess_")
        assert session_data["projects"] == ["demo", "other"]

        # 4. Use session token to access protected operator endpoint
        session_headers = {"X-Laya-Approval-Token": session_data["session_token"]}
        status_res = client.get("/api/v1/operator/status", headers=session_headers)
        assert status_res.status_code == 200
        assert status_res.json()["api_status"] == "healthy"

        projects_res = client.get("/api/v1/operator/projects", headers=session_headers)
        assert projects_res.status_code == 200
        assert projects_res.json() == ["demo", "other"]

        # 5. Disconnect session and verify token is revoked
        disc = client.post("/api/v1/operator/session/disconnect", headers=session_headers)
        assert disc.status_code == 200
        assert disc.json()["status"] == "disconnected"

        revoked_status = client.get("/api/v1/operator/status", headers=session_headers)
        assert revoked_status.status_code == 401

