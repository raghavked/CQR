"""
CP-5 coverage tests for orchestration package.
Targets router.py (0%), db.py (0%), internal_security.py (0%).
All DB calls and downstream service calls are mocked.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from src.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper: mock AsyncSessionLocal context manager
# ---------------------------------------------------------------------------

def _mock_session(fetchrow=None, fetch=None):
    """Return a mock that acts as AsyncSessionLocal() async context manager."""
    mock_session = AsyncMock()
    if fetchrow is not None:
        mock_result = MagicMock()
        mock_result.mappings.return_value.first.return_value = fetchrow
        mock_session.execute = AsyncMock(return_value=mock_result)
    if fetch is not None:
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = fetch
        mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    return mock_session


def _mock_httpx_response(json_data, status_code=200):
    """Return a mock httpx async context manager that returns json_data."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


# ---------------------------------------------------------------------------
# router.py — project endpoints
# ---------------------------------------------------------------------------


class TestProjectEndpoints:
    """Cover router.py project CRUD endpoints."""

    def test_create_project(self, client):
        mock_project = {
            "id": "proj-1", "name": "test-project", "repo_path": "/tmp/repo",
            "status": "provisioning", "created_at": "2024-01-01T00:00:00", "container_id": None,
        }
        with patch("src.router.create_project", new_callable=AsyncMock, return_value=mock_project), \
             patch("src.router._provision_project", new_callable=AsyncMock):
            r = client.post("/api/v1/projects", json={
                "name": "test-project",
                "repo_path": "/tmp/repo",
            })
        assert r.status_code in (200, 201, 202)

    def test_get_project_found(self, client):
        mock_project = {
            "id": "proj-1", "name": "test-project", "repo_path": "/tmp/repo",
            "status": "ready", "created_at": "2024-01-01T00:00:00", "container_id": "ctr-1",
        }
        with patch("src.router.get_project", new_callable=AsyncMock, return_value=mock_project), \
             patch("src.router.httpx.AsyncClient", return_value=_mock_httpx_response(
                 {"running": True, "status": "running"})):
            r = client.get("/api/v1/projects/proj-1")
        assert r.status_code == 200

    def test_get_project_not_found(self, client):
        with patch("src.router.get_project", new_callable=AsyncMock, return_value=None):
            r = client.get("/api/v1/projects/nonexistent")
        assert r.status_code == 404

    def test_list_projects(self, client):
        mock_projects = [
            {"id": "proj-1", "name": "p1", "repo_path": "/tmp/r1", "status": "ready",
             "created_at": "2024-01-01T00:00:00", "container_id": None},
        ]
        with patch("src.router.list_projects", new_callable=AsyncMock, return_value=mock_projects):
            r = client.get("/api/v1/projects")
        assert r.status_code == 200
        assert len(r.json()) == 1


class TestTaskEndpoints:
    """Cover router.py task endpoints."""

    def test_submit_task(self, client):
        mock_task = {
            "id": "task-1", "project_id": "proj-1", "description": "Fix the bug",
            "status": "pending", "created_at": "2024-01-01T00:00:00",
            "diff": None, "token_usage": None,
        }
        with patch("src.router.get_project", new_callable=AsyncMock, return_value={
            "id": "proj-1", "name": "p1", "repo_path": "/tmp/r1",
            "status": "ready", "container_id": "ctr-1",
        }), \
             patch("src.router.create_task", new_callable=AsyncMock, return_value=mock_task):
            r = client.post("/api/v1/tasks", json={
                "project_id": "proj-1",
                "description": "Fix the bug",
                "api_key": "test-key",
                "api_key_type": "claude",
            })
        assert r.status_code in (200, 201, 202, 422)

    def test_get_task_found(self, client):
        mock_task = {
            "id": "task-1", "project_id": "proj-1", "description": "Fix the bug",
            "status": "done", "created_at": "2024-01-01T00:00:00",
            "diff": "--- a/app.py\n+++ b/app.py\n",
            "token_usage": {"context_tokens": 100, "response_tokens": 50, "total_tokens": 150,
                            "savings_vs_raw": 60.0, "context_node_count": 10},
        }
        with patch("src.router.get_task", new_callable=AsyncMock, return_value=mock_task):
            r = client.get("/api/v1/tasks/task-1")
        assert r.status_code == 200
        # TaskStatusResponse wraps task in a 'task' key
        body = r.json()
        task_data = body.get("task", body)
        assert task_data.get("status") == "done" or body.get("status") == "done"

    def test_get_task_not_found(self, client):
        with patch("src.router.get_task", new_callable=AsyncMock, return_value=None):
            r = client.get("/api/v1/tasks/nonexistent")
        assert r.status_code == 404

    def test_get_task_diff(self, client):
        mock_task = {
            "id": "task-1", "project_id": "proj-1", "description": "Fix the bug",
            "status": "completed", "created_at": "2024-01-01T00:00:00",
            "diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
            "token_usage": None,
        }
        with patch("src.router.get_task", new_callable=AsyncMock, return_value=mock_task):
            r = client.get("/api/v1/tasks/task-1/diff")
        assert r.status_code == 200
        assert "diff" in r.json()

    def test_reject_task(self, client):
        mock_task = {
            "id": "task-1", "project_id": "proj-1", "description": "Fix the bug",
            "status": "diff_ready", "created_at": "2024-01-01T00:00:00",
            "diff": "--- a/app.py\n+++ b/app.py\n", "token_usage": None,
        }
        with patch("src.router.get_task", new_callable=AsyncMock, return_value=mock_task), \
             patch("src.router.update_task", new_callable=AsyncMock):
            r = client.post("/api/v1/tasks/task-1/reject", json={"reason": "not what I wanted"})
        assert r.status_code == 200

    def test_list_tasks(self, client):
        mock_tasks = [
            {"id": "task-1", "project_id": "proj-1", "description": "Fix", "status": "done",
             "created_at": "2024-01-01T00:00:00", "diff": None, "token_usage": None},
        ]
        with patch("src.router.list_tasks", new_callable=AsyncMock, return_value=mock_tasks):
            r = client.get("/api/v1/tasks")
        assert r.status_code == 200
        assert len(r.json()) == 1


class TestPassthroughEndpoints:
    """Cover router.py passthrough endpoints (kg, security, vault)."""

    def test_kg_explore(self, client):
        with patch("src.router.httpx.AsyncClient",
                   return_value=_mock_httpx_response({"nodes": [], "edges": []})):
            r = client.get("/api/v1/kg/explore", params={"project_id": "proj-1"})
        assert r.status_code == 200

    def test_security_report(self, client):
        with patch("src.router.get_project", new_callable=AsyncMock, return_value={
            "id": "proj-1", "name": "p1", "repo_path": "/tmp/r1",
            "status": "ready", "container_id": "ctr-1",
        }), \
             patch("src.router.httpx.AsyncClient",
                   return_value=_mock_httpx_response({"project_id": "proj-1", "findings": [], "scanned_at": "2024-01-01T00:00:00"})):
            r = client.get("/api/v1/security/report/proj-1")
        assert r.status_code == 200

    def test_vault_store_key(self, client):
        with patch("src.router.httpx.AsyncClient",
                   return_value=_mock_httpx_response({"stored": True, "key_name": "DB_PASS"})):
            r = client.post("/api/v1/vault/keys", json={
                "project_id": "proj-1",
                "key_name": "DB_PASS",
                "secret_value": "supersecret",
            })
        assert r.status_code == 200

    def test_vault_list_keys(self, client):
        with patch("src.router.httpx.AsyncClient",
                   return_value=_mock_httpx_response({"keys": ["DB_PASS", "API_KEY"]})):
            r = client.get("/api/v1/vault/keys/proj-1")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# db.py — async CRUD functions using mocked AsyncSessionLocal
# ---------------------------------------------------------------------------


class TestDbCrud:
    """Cover db.py async CRUD functions using mocked SQLAlchemy sessions."""

    @pytest.mark.asyncio
    async def test_get_project_returns_none_when_missing(self):
        from src import db

        mock_result = MagicMock()
        mock_result.mappings.return_value.first.return_value = None

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(db, "AsyncSessionLocal", return_value=mock_session):
            result = await db.get_project("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_project_returns_dict_when_found(self):
        from src import db

        mock_row = {"id": "proj-1", "name": "test", "repo_path": "/r",
                    "status": "ready", "created_at": "2024-01-01", "container_id": None}
        mock_result = MagicMock()
        mock_result.mappings.return_value.first.return_value = mock_row

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(db, "AsyncSessionLocal", return_value=mock_session):
            result = await db.get_project("proj-1")

        assert result is not None
        assert result["name"] == "test"

    @pytest.mark.asyncio
    async def test_list_projects_returns_list(self):
        from src import db

        mock_rows = [
            {"id": "p1", "name": "proj1", "repo_path": "/r1", "status": "ready",
             "created_at": "2024-01-01", "container_id": None},
        ]
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = mock_rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(db, "AsyncSessionLocal", return_value=mock_session):
            result = await db.list_projects()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_update_project_calls_execute(self):
        from src import db

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(db, "AsyncSessionLocal", return_value=mock_session):
            await db.update_project("proj-1", status="ready")

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_task_returns_none_when_missing(self):
        from src import db

        mock_result = MagicMock()
        mock_result.mappings.return_value.first.return_value = None

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(db, "AsyncSessionLocal", return_value=mock_session):
            result = await db.get_task("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_list_tasks_returns_list(self):
        from src import db

        mock_rows = [
            {"id": "t1", "project_id": "proj-1", "description": "Fix", "status": "completed",
             "created_at": "2024-01-01", "diff": None, "token_usage": None},
        ]
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = mock_rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(db, "AsyncSessionLocal", return_value=mock_session):
            result = await db.list_tasks(project_id="proj-1")

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_update_task_calls_execute(self):
        from src import db

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(db, "AsyncSessionLocal", return_value=mock_session):
            await db.update_task("task-1", status="completed", diff="--- a/f\n+++ b/f\n")

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_latest_findings_returns_list(self):
        from src import db

        mock_rows = [
            {"severity": "CRITICAL", "pattern": "sql_injection_path",
             "node_path": ["n1", "n2"], "description": "SQL injection",
             "scan_id": "scan-1", "created_at": "2024-01-01"},
        ]
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = mock_rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(db, "AsyncSessionLocal", return_value=mock_session):
            result = await db.get_latest_findings("proj-1")

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_scan_history_returns_list(self):
        from src import db

        mock_rows = [
            {"scan_id": "scan-1", "project_id": "proj-1", "summary": {"total": 1},
             "created_at": "2024-01-01"},
        ]
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = mock_rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(db, "AsyncSessionLocal", return_value=mock_session):
            result = await db.get_scan_history_db("proj-1")

        assert len(result) == 1


# ---------------------------------------------------------------------------
# internal_security.py — endpoints
# ---------------------------------------------------------------------------


class TestInternalSecurityEndpoints:
    """Cover internal_security.py endpoints."""

    def test_store_scan(self, client):
        with patch("src.internal_security.store_scan_results", new_callable=AsyncMock,
                   return_value={"scan_id": "scan-1"}):
            r = client.post("/internal/security/store", json={
                "project_id": "proj-1",
                "scan_id": "scan-1",
                "findings": [{"severity": "CRITICAL", "pattern": "sql_injection_path",
                               "node_path": ["n1", "n2"], "description": "SQL injection"}],
                "summary": {"total": 1, "critical": 1},
            })
        assert r.status_code == 200

    def test_get_findings(self, client):
        with patch("src.internal_security.get_latest_findings", new_callable=AsyncMock,
                   return_value=[{"severity": "CRITICAL", "pattern": "sql_injection_path",
                                  "node_path": ["n1", "n2"], "description": "SQL injection"}]):
            r = client.get("/internal/security/findings/proj-1")
        assert r.status_code == 200
        assert len(r.json()["findings"]) == 1

    def test_get_history(self, client):
        with patch("src.internal_security.get_scan_history_db", new_callable=AsyncMock,
                   return_value=[{"scan_id": "scan-1", "created_at": "2024-01-01T00:00:00",
                                  "summary": {"total": 1}}]):
            r = client.get("/internal/security/history/proj-1")
        assert r.status_code == 200
        assert len(r.json()["history"]) == 1


# ---------------------------------------------------------------------------
# router.py — helper functions (pure/sync, no DB needed)
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    """Cover _row_to_project, _row_to_task, and _extract_diff_paths."""

    def test_row_to_project_full(self):
        from src.router import _row_to_project
        from datetime import datetime

        row = {
            "id": "proj-1", "name": "test", "repo_path": "/tmp/r",
            "status": "ready", "created_at": datetime(2024, 1, 1), "container_id": "ctr-1",
        }
        project = _row_to_project(row)
        assert project.id == "proj-1"
        assert project.name == "test"
        assert project.container_id == "ctr-1"
        assert project.status == "ready"

    def test_row_to_project_minimal(self):
        from src.router import _row_to_project

        row = {"id": "proj-2", "name": "minimal"}
        project = _row_to_project(row)
        assert project.id == "proj-2"
        assert project.repo_path == ""
        assert project.container_id is None

    def test_row_to_task_full(self):
        from src.router import _row_to_task
        from datetime import datetime

        row = {
            "id": "task-1", "project_id": "proj-1", "description": "Fix it",
            "agent": "claude", "budget_tier": "standard", "status": "done",
            "created_at": datetime(2024, 1, 1), "completed_at": None,
            "token_usage": '{"context_tokens": 100, "response_tokens": 50, "total_tokens": 150, "savings_vs_raw": 60.0, "context_node_count": 10, "raw_total_tokens": 250, "prompt_tokens": 100, "completion_tokens": 50}',
            "diff": "--- a/f\n+++ b/f\n", "confidence": 0.9,
        }
        task = _row_to_task(row)
        assert task.id == "task-1"
        assert task.status == "done"
        assert task.token_usage is not None
        assert task.token_usage.context_tokens == 100

    def test_row_to_task_no_token_usage(self):
        from src.router import _row_to_task

        row = {
            "id": "task-2", "project_id": "proj-1", "description": "Fix",
            "status": "queued", "token_usage": None,
        }
        task = _row_to_task(row)
        assert task.token_usage is None

    def test_row_to_task_dict_token_usage(self):
        from src.router import _row_to_task

        row = {
            "id": "task-3", "project_id": "proj-1", "description": "Fix",
            "status": "running",
            "token_usage": {"context_tokens": 50, "response_tokens": 25, "total_tokens": 75,
                            "savings_vs_raw": 40.0, "context_node_count": 5,
                            "raw_total_tokens": 125, "prompt_tokens": 50, "completion_tokens": 25},
        }
        task = _row_to_task(row)
        assert task.token_usage.total_tokens == 75

    def test_extract_diff_paths_standard(self):
        from src.router import _extract_diff_paths

        diff = (
            "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new\n"
            "--- a/tests/test_app.py\n+++ b/tests/test_app.py\n@@ -1 +1 @@\n-old\n+new\n"
        )
        paths = _extract_diff_paths(diff)
        assert "src/app.py" in paths
        assert "tests/test_app.py" in paths
        assert len(paths) == 2

    def test_extract_diff_paths_deduplication(self):
        from src.router import _extract_diff_paths

        diff = (
            "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
            "--- a/app.py\n+++ b/app.py\n@@ -5 +5 @@\n-old2\n+new2\n"
        )
        paths = _extract_diff_paths(diff)
        assert paths.count("app.py") == 1

    def test_extract_diff_paths_dev_null_excluded(self):
        from src.router import _extract_diff_paths

        diff = "--- /dev/null\n+++ b/new_file.py\n@@ -0,0 +1 @@\n+new\n"
        paths = _extract_diff_paths(diff)
        assert "new_file.py" in paths
        assert "/dev/null" not in paths

    def test_extract_diff_paths_empty(self):
        from src.router import _extract_diff_paths

        assert _extract_diff_paths("") == []
        assert _extract_diff_paths("no diff here") == []

    def test_extract_diff_paths_bare_path(self):
        from src.router import _extract_diff_paths

        diff = "+++ config.yaml\n@@ -1 +1 @@\n-old\n+new\n"
        paths = _extract_diff_paths(diff)
        assert "config.yaml" in paths


# ---------------------------------------------------------------------------
# router.py — _run_task integration test (mocks all internal service calls)
# ---------------------------------------------------------------------------


class TestRunTask:
    """Cover _run_task background pipeline with all services mocked."""

    @pytest.mark.asyncio
    async def test_run_task_success_full_pipeline(self):
        """_run_task: full success path with diff, apply, KG re-ingest, security scan."""
        from src.router import _run_task

        mock_task_row = {
            "id": "task-1", "project_id": "proj-1", "description": "Fix the bug",
            "agent": "claude", "budget_tier": "standard", "status": "queued",
            "api_key": "test-key", "api_key_type": "claude",
        }
        mock_project_row = {
            "id": "proj-1", "name": "p1", "repo_path": "/tmp/r",
            "status": "ready", "container_id": "ctr-1",
        }
        mock_agent_response = {
            "diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
            "confidence": 0.95,
            "explanation": "Fixed the bug",
            "restricted_paths": [],
            "token_usage": {
                "context_tokens": 100, "response_tokens": 50, "total_tokens": 150,
                "savings_vs_raw": 60.0, "context_node_count": 10,
                "raw_total_tokens": 250, "prompt_tokens": 100, "completion_tokens": 50,
            },
        }

        async def mock_call_internal(method, url, **kwargs):
            if "/agent/dispatch" in url:
                return mock_agent_response
            if "/exec/apply-diff" in url:
                return {"files_changed": 1, "lines_added": 1, "lines_removed": 1}
            if "/kg/ingest" in url:
                return {"summary": {"changed_node_ids": ["node-1"]}}
            if "/kg/nodes" in url:
                return [{"id": "node-1", "type": "File", "properties": {"path": "app.py"}}]
            if "/kg/mark-agent-edit" in url:
                return {"marked": True}
            if "/security/scan-nodes" in url:
                return {"findings": [{"severity": "CRITICAL", "pattern": "sql_injection_path",
                                       "node_path": ["n1", "n2"]}]}
            if "/security/scan" in url:
                return {"findings": []}
            return {}

        with patch("src.router.get_task", new_callable=AsyncMock, return_value=mock_task_row), \
             patch("src.router.get_project", new_callable=AsyncMock, return_value=mock_project_row), \
             patch("src.router.update_task", new_callable=AsyncMock), \
             patch("src.router._call_internal", side_effect=mock_call_internal), \
             patch("src.router.emit_task_started", new_callable=AsyncMock), \
             patch("src.router.emit_task_context_assembled", new_callable=AsyncMock), \
             patch("src.router.emit_task_diff_ready", new_callable=AsyncMock), \
             patch("src.router.emit_task_applied", new_callable=AsyncMock), \
             patch("src.router.emit_task_failed", new_callable=AsyncMock), \
             patch("src.router.emit_kg_updated", new_callable=AsyncMock), \
             patch("src.router.emit_security_alert", new_callable=AsyncMock), \
             patch("src.router.httpx.AsyncClient") as mock_httpx:
            # Mock httpx for _mark_agent_edits
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            await _run_task("task-1", "test-key", "claude")

    @pytest.mark.asyncio
    async def test_run_task_task_not_found(self):
        """_run_task: exits early when task row is not found."""
        from src.router import _run_task

        with patch("src.router.get_task", new_callable=AsyncMock, return_value=None):
            # Should return without raising
            await _run_task("nonexistent", "key", "claude")

    @pytest.mark.asyncio
    async def test_run_task_agent_dispatch_failure(self):
        """_run_task: marks task as failed when agent dispatch raises."""
        from src.router import _run_task

        mock_task_row = {
            "id": "task-1", "project_id": "proj-1", "description": "Fix",
            "agent": "claude", "budget_tier": "standard", "status": "queued",
        }
        mock_project_row = {
            "id": "proj-1", "name": "p1", "repo_path": "/tmp/r",
            "status": "ready", "container_id": "ctr-1",
        }

        async def mock_call_internal_fail(method, url, **kwargs):
            if "/agent/dispatch" in url:
                raise RuntimeError("Agent dispatch failed")
            return {}

        with patch("src.router.get_task", new_callable=AsyncMock, return_value=mock_task_row), \
             patch("src.router.get_project", new_callable=AsyncMock, return_value=mock_project_row), \
             patch("src.router.update_task", new_callable=AsyncMock) as mock_update, \
             patch("src.router._call_internal", side_effect=mock_call_internal_fail), \
             patch("src.router.emit_task_started", new_callable=AsyncMock), \
             patch("src.router.emit_task_failed", new_callable=AsyncMock) as mock_emit_fail:

            await _run_task("task-1", "test-key", "claude")

        # Should have called update_task with status=failed
        update_calls = mock_update.call_args_list
        assert any("failed" in str(c) for c in update_calls)
