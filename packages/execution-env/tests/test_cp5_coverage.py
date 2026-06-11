"""
CP-5 coverage tests for execution-env.
Targets api.py (0%), terminal/executor.py (27%), containers/manager.py (42%).
All Docker calls are mocked — no real Docker daemon required.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from src.api import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# api.py endpoint coverage — correct route paths from source
# ---------------------------------------------------------------------------


class TestContainerApiEndpoints:
    """Cover api.py container lifecycle endpoints."""

    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_create_container(self, client):
        with patch("src.api.create_container", return_value={"container_id": "abc123", "status": "created"}):
            r = client.post("/exec/container/create", json={"project_id": "proj-1", "repo_path": "/tmp/repo"})
        assert r.status_code == 200
        assert r.json()["container_id"] == "abc123"

    def test_start_container(self, client):
        with patch("src.api.start_container", return_value={"status": "started", "container_id": "ctr-1"}):
            r = client.post("/exec/container/ctr-1/start")
        assert r.status_code == 200

    def test_stop_container(self, client):
        with patch("src.api.stop_container", return_value={"status": "stopped"}):
            r = client.post("/exec/container/ctr-1/stop")
        assert r.status_code == 200

    def test_restart_container(self, client):
        with patch("src.api.restart_container", return_value={"status": "running"}):
            r = client.post("/exec/container/ctr-1/restart")
        assert r.status_code == 200

    def test_remove_container(self, client):
        with patch("src.api.remove_container", return_value={"status": "removed"}):
            r = client.delete("/exec/container/ctr-1")
        assert r.status_code == 200

    def test_get_container_status(self, client):
        with patch("src.api.get_container_status", return_value={"status": "running", "running": True, "container_id": "ctr-1"}):
            r = client.get("/exec/container/ctr-1/status")
        assert r.status_code == 200
        assert r.json()["running"] is True

    def test_get_container_logs(self, client):
        with patch("src.api.get_container_logs", return_value={"container_id": "ctr-1", "logs": "line1\nline2\n"}):
            r = client.get("/exec/container/ctr-1/logs")
        assert r.status_code == 200
        assert "logs" in r.json()

    def test_get_container_by_project_found(self, client):
        with patch("src.api.get_container_by_project", return_value={"container_id": "ctr-1", "status": "running"}):
            r = client.get("/exec/container/by-project/proj-1")
        assert r.status_code == 200
        assert r.json()["container_id"] == "ctr-1"

    def test_get_container_by_project_not_found(self, client):
        with patch("src.api.get_container_by_project", return_value={}):
            r = client.get("/exec/container/by-project/proj-missing")
        assert r.status_code == 404


class TestFileApiEndpoints:
    """Cover api.py file operation endpoints."""

    def test_write_file(self, client):
        with patch("src.api.write_file", return_value={"written": True, "path": "src/app.py"}):
            r = client.post("/exec/write-file", json={
                "container_id": "ctr-1",
                "path": "src/app.py",
                "content": "print('hello')",
            })
        assert r.status_code == 200

    def test_read_file(self, client):
        with patch("src.api.read_file", return_value={"content": "print('hello')", "path": "src/app.py"}):
            r = client.get("/exec/read-file", params={"container_id": "ctr-1", "path": "src/app.py"})
        assert r.status_code == 200
        assert r.json()["content"] == "print('hello')"

    def test_list_directory(self, client):
        with patch("src.api.list_directory", return_value={"entries": [{"name": "app.py", "type": "file"}]}):
            r = client.get("/exec/ls", params={"container_id": "ctr-1", "path": "."})
        assert r.status_code == 200
        assert len(r.json()["entries"]) == 1

    def test_apply_diff(self, client):
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
        with patch("src.api.apply_diff", return_value={"applied": True, "files_changed": 1}):
            r = client.post("/exec/apply-diff", json={
                "container_id": "ctr-1",
                "diff": diff,
                "project_id": "proj-1",
                "task_id": "task-1",
            })
        assert r.status_code == 200
        assert r.json()["files_changed"] == 1

    def test_write_file_path_traversal_rejected(self, client):
        with patch("src.api.write_file", side_effect=ValueError("path traversal")):
            r = client.post("/exec/write-file", json={
                "container_id": "ctr-1",
                "path": "../../../etc/passwd",
                "content": "evil",
            })
        assert r.status_code == 400


class TestGitApiEndpoints:
    """Cover api.py git operation endpoints."""

    def test_git_status(self, client):
        with patch("src.api.git_status", return_value={"changes": [], "branch": "main", "clean": True}):
            r = client.get("/exec/git/status", params={"container_id": "ctr-1"})
        assert r.status_code == 200
        assert r.json()["clean"] is True

    def test_git_diff(self, client):
        with patch("src.api.git_diff", return_value={"diff": "--- a/app.py\n+++ b/app.py\n"}):
            r = client.get("/exec/git/diff", params={"container_id": "ctr-1"})
        assert r.status_code == 200
        assert "diff" in r.json()

    def test_git_commit(self, client):
        with patch("src.api.git_commit", return_value={"committed": True, "sha": "abc123"}):
            r = client.post("/exec/git/commit", json={
                "container_id": "ctr-1",
                "message": "fix: apply agent patch",
                "author": "CQR Agent",
            })
        assert r.status_code == 200
        assert r.json()["committed"] is True

    def test_git_log(self, client):
        with patch("src.api.git_log", return_value={"commits": [{"sha": "abc", "message": "init"}]}):
            r = client.get("/exec/git/log", params={"container_id": "ctr-1", "n": 5})
        assert r.status_code == 200
        assert len(r.json()["commits"]) == 1


class TestExecApiEndpoints:
    """Cover api.py shell execution endpoints."""

    def test_run_command(self, client):
        with patch("src.api.run_command", return_value={
            "exit_code": 0, "stdout": "hello\n", "stderr": "", "rejected": False, "session_id": "s1"
        }):
            r = client.post("/exec/run", json={
                "container_id": "ctr-1",
                "command": "echo hello",
                "project_id": "proj-1",
            })
        assert r.status_code == 200
        assert r.json()["exit_code"] == 0

    def test_run_command_rejected(self, client):
        with patch("src.api.run_command", return_value={
            "exit_code": 1, "stdout": "", "stderr": "Command rejected", "rejected": True, "session_id": "s1"
        }):
            r = client.post("/exec/run", json={
                "container_id": "ctr-1",
                "command": "rm -rf /",
                "project_id": "proj-1",
            })
        assert r.status_code == 200
        assert r.json()["rejected"] is True


# ---------------------------------------------------------------------------
# terminal/executor.py coverage
# ---------------------------------------------------------------------------


class TestCommandSanitizer:
    """Cover terminal/executor.py sanitize_command function."""

    def test_safe_command_passes(self):
        from src.terminal.executor import sanitize_command
        ok, reason = sanitize_command("echo hello", "s1")
        assert ok is True
        assert reason == ""

    def test_rm_rf_root_rejected(self):
        from src.terminal.executor import sanitize_command
        ok, reason = sanitize_command("rm -rf /", "s1")
        assert ok is False
        assert reason != ""

    def test_shutdown_rejected(self):
        from src.terminal.executor import sanitize_command
        ok, reason = sanitize_command("sudo shutdown now", "s1")
        assert ok is False

    def test_reboot_rejected(self):
        from src.terminal.executor import sanitize_command
        ok, reason = sanitize_command("reboot", "s1")
        assert ok is False

    def test_mkfs_rejected(self):
        from src.terminal.executor import sanitize_command
        ok, reason = sanitize_command("mkfs.ext4 /dev/sda", "s1")
        assert ok is False

    def test_cqr_write_rejected(self):
        from src.terminal.executor import sanitize_command
        ok, reason = sanitize_command("echo x > /cqr/secrets", "s1")
        assert ok is False

    def test_python_command_allowed(self):
        from src.terminal.executor import sanitize_command
        ok, reason = sanitize_command("python3 -m pytest tests/", "s1")
        assert ok is True

    def test_git_command_allowed(self):
        from src.terminal.executor import sanitize_command
        ok, reason = sanitize_command("git status", "s1")
        assert ok is True

    def test_pip_install_allowed(self):
        from src.terminal.executor import sanitize_command
        ok, reason = sanitize_command("pip install requests", "s1")
        assert ok is True


class TestRunCommand:
    """Cover terminal/executor.py run_command function."""

    def test_run_command_success(self):
        from src.terminal.executor import run_command

        mock_container = MagicMock()
        mock_container.exec_run.return_value = (0, (b"hello\n", b""))

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("src.terminal.executor.docker.from_env", return_value=mock_docker):
            result = run_command("ctr-1", "echo hello", "proj-1")

        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert result["rejected"] is False

    def test_run_command_rejected_by_sanitizer(self):
        from src.terminal.executor import run_command

        with patch("src.terminal.executor.docker.from_env") as mock_docker:
            result = run_command("ctr-1", "rm -rf /", "proj-1")

        assert result["rejected"] is True
        assert result["exit_code"] == 1
        mock_docker.assert_not_called()

    def test_run_command_container_not_found(self):
        from src.terminal.executor import run_command
        import docker

        mock_docker = MagicMock()
        mock_docker.containers.get.side_effect = docker.errors.NotFound("ctr-1")

        with patch("src.terminal.executor.docker.from_env", return_value=mock_docker):
            result = run_command("ctr-1", "echo hello", "proj-1")

        assert result["exit_code"] == 1
        assert "not found" in result["stderr"].lower()

    def test_run_command_nonzero_exit(self):
        from src.terminal.executor import run_command

        mock_container = MagicMock()
        mock_container.exec_run.return_value = (1, (b"", b"error: command failed\n"))

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("src.terminal.executor.docker.from_env", return_value=mock_docker):
            result = run_command("ctr-1", "false", "proj-1")

        assert result["exit_code"] == 1
        assert "error" in result["stderr"].lower()


# ---------------------------------------------------------------------------
# containers/manager.py coverage
# ---------------------------------------------------------------------------


class TestContainerManager:
    """Cover containers/manager.py — lifecycle operations."""

    def test_create_container_success(self):
        from src.containers.manager import create_container

        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_container.short_id = "abc123"
        mock_container.status = "created"

        mock_docker = MagicMock()
        mock_docker.containers.create.return_value = mock_container

        with patch("src.containers.manager.docker.from_env", return_value=mock_docker):
            result = create_container("proj-1", "/tmp/repo")

        assert result["container_id"] == "abc123def456"

    def test_get_container_by_project_found(self):
        from src.containers.manager import get_container_by_project

        mock_container = MagicMock()
        mock_container.id = "ctr-abc"
        mock_container.status = "running"
        mock_container.attrs = {
            "State": {"Running": True, "StartedAt": "2024-01-01T00:00:00Z", "ExitCode": 0},
            "Config": {"Image": "cqr-sandbox:latest"},
        }

        mock_docker = MagicMock()
        mock_docker.containers.list.return_value = [mock_container]
        mock_docker.containers.get.return_value = mock_container

        with patch("src.containers.manager.docker.from_env", return_value=mock_docker):
            result = get_container_by_project("proj-1")

        assert result.get("container_id") == "ctr-abc" or result.get("status") is not None

    def test_get_container_by_project_not_found(self):
        from src.containers.manager import get_container_by_project

        mock_docker = MagicMock()
        mock_docker.containers.list.return_value = []

        with patch("src.containers.manager.docker.from_env", return_value=mock_docker):
            result = get_container_by_project("proj-missing")

        assert result == {}

    def test_stop_container_success(self):
        from src.containers.manager import stop_container

        mock_container = MagicMock()
        mock_container.status = "exited"

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("src.containers.manager.docker.from_env", return_value=mock_docker):
            result = stop_container("ctr-1")

        mock_container.stop.assert_called_once()
        assert result.get("status") is not None

    def test_restart_container_success(self):
        from src.containers.manager import restart_container

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.attrs = {
            "State": {"Running": True, "StartedAt": "2024-01-01T00:00:00Z", "ExitCode": 0},
            "Config": {"Image": "cqr-sandbox:latest"},
        }

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("src.containers.manager.docker.from_env", return_value=mock_docker):
            result = restart_container("ctr-1")

        mock_container.restart.assert_called_once()

    def test_remove_container_success(self):
        from src.containers.manager import remove_container

        mock_container = MagicMock()
        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("src.containers.manager.docker.from_env", return_value=mock_docker):
            result = remove_container("ctr-1")

        mock_container.remove.assert_called_once()

    def test_get_container_status_running(self):
        from src.containers.manager import get_container_status

        mock_container = MagicMock()
        mock_container.id = "ctr-1"
        mock_container.status = "running"
        mock_container.attrs = {
            "State": {"Running": True, "StartedAt": "2024-01-01T00:00:00Z", "ExitCode": 0},
            "Config": {"Image": "cqr-sandbox:latest"},
        }

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("src.containers.manager.docker.from_env", return_value=mock_docker):
            result = get_container_status("ctr-1")

        assert result["running"] is True

    def test_get_container_logs(self):
        from src.containers.manager import get_container_logs

        mock_container = MagicMock()
        mock_container.logs.return_value = b"line1\nline2\nline3\n"

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("src.containers.manager.docker.from_env", return_value=mock_docker):
            result = get_container_logs("ctr-1", tail=10)

        assert "logs" in result
        assert "line1" in result["logs"]


# ---------------------------------------------------------------------------
# terminal/executor.py — stream_command async generator coverage
# ---------------------------------------------------------------------------


class TestStreamCommand:
    """Cover terminal/executor.py stream_command async generator."""

    @pytest.mark.asyncio
    async def test_stream_command_rejected_yields_exit(self):
        """stream_command yields stderr + exit chunks when command is rejected."""
        from src.terminal.executor import stream_command

        chunks = []
        async for chunk in stream_command("ctr-1", "rm -rf /", "proj-1"):
            chunks.append(chunk)

        types = [c["type"] for c in chunks]
        assert "stderr" in types
        assert "exit" in types
        exit_chunk = next(c for c in chunks if c["type"] == "exit")
        assert exit_chunk["data"] == "1"
        assert exit_chunk["rejected"] is True

    @pytest.mark.asyncio
    async def test_stream_command_success_yields_stdout_and_exit(self):
        """stream_command yields stdout chunks and exit=0 for a safe command."""
        from src.terminal.executor import stream_command

        mock_container = MagicMock()
        mock_container.id = "ctr-1"

        mock_exec_id = {"Id": "exec-abc"}
        output_gen = iter([(b"hello\n", None), (None, b"warn\n")])

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container
        mock_docker.api.exec_create.return_value = mock_exec_id
        mock_docker.api.exec_start.return_value = output_gen
        mock_docker.api.exec_inspect.return_value = {"ExitCode": 0}

        with patch("src.terminal.executor.docker.from_env", return_value=mock_docker):
            chunks = []
            async for chunk in stream_command("ctr-1", "echo hello", "proj-1"):
                chunks.append(chunk)

        types = [c["type"] for c in chunks]
        assert "stdout" in types
        assert "exit" in types
        exit_chunk = next(c for c in chunks if c["type"] == "exit")
        assert exit_chunk["data"] == "0"

    @pytest.mark.asyncio
    async def test_stream_command_container_not_found(self):
        """stream_command yields error chunks when container is not found."""
        from src.terminal.executor import stream_command
        import docker

        mock_docker = MagicMock()
        mock_docker.containers.get.side_effect = docker.errors.NotFound("ctr-1")

        with patch("src.terminal.executor.docker.from_env", return_value=mock_docker):
            chunks = []
            async for chunk in stream_command("ctr-1", "echo hello", "proj-1"):
                chunks.append(chunk)

        types = [c["type"] for c in chunks]
        assert "stderr" in types
        assert "exit" in types

    @pytest.mark.asyncio
    async def test_stream_command_generic_error(self):
        """stream_command yields error chunks on unexpected exceptions."""
        from src.terminal.executor import stream_command

        mock_docker = MagicMock()
        mock_docker.containers.get.side_effect = RuntimeError("unexpected error")

        with patch("src.terminal.executor.docker.from_env", return_value=mock_docker):
            chunks = []
            async for chunk in stream_command("ctr-1", "echo hello", "proj-1"):
                chunks.append(chunk)

        types = [c["type"] for c in chunks]
        assert "stderr" in types
        assert "exit" in types
