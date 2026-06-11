"""
CP-2 tests for the Execution Environment package.

Tests:
  - Container lifecycle: create, start, stop, restart, remove, status
  - File system: write, read, list, path traversal prevention
  - Shell execution: blocking run, sanitizer deny list
  - Git operations: status, diff, commit, log
  - apply_diff statistics parsing

These tests use unittest.mock to avoid requiring a live Docker daemon.
Integration tests that require a real Docker socket are marked with
@pytest.mark.integration and skipped in CI unless DOCKER_AVAILABLE=1.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.fs.operations import _safe_workspace_path, apply_diff
from src.terminal.executor import sanitize_command


# ---------------------------------------------------------------------------
# Path traversal prevention
# ---------------------------------------------------------------------------


class TestSafeWorkspacePath:
    def test_relative_path_is_rooted(self):
        result = _safe_workspace_path("src/main.py")
        assert result == "/workspace/src/main.py"

    def test_absolute_workspace_path_accepted(self):
        result = _safe_workspace_path("/workspace/src/main.py")
        assert "/workspace" in result

    def test_traversal_with_dotdot_rejected(self):
        with pytest.raises(ValueError, match="traversal"):
            _safe_workspace_path("../../etc/passwd")

    def test_traversal_with_absolute_escape_rejected(self):
        with pytest.raises(ValueError, match="traversal"):
            _safe_workspace_path("/etc/passwd")

    def test_nested_relative_path(self):
        result = _safe_workspace_path("a/b/c/d.py")
        assert result == "/workspace/a/b/c/d.py"

    def test_empty_path_returns_workspace(self):
        # Empty path should not raise; returns /workspace itself
        result = _safe_workspace_path("")
        assert result == "/workspace"


# ---------------------------------------------------------------------------
# Command sanitizer
# ---------------------------------------------------------------------------


class TestCommandSanitizer:
    def test_safe_command_allowed(self):
        ok, reason = sanitize_command("ls -la /workspace", "sess-1")
        assert ok is True
        assert reason == ""

    def test_rm_rf_root_denied(self):
        ok, reason = sanitize_command("rm -rf /", "sess-2")
        assert ok is False
        assert "denied" in reason.lower()

    def test_shutdown_denied(self):
        ok, reason = sanitize_command("shutdown -h now", "sess-3")
        assert ok is False

    def test_reboot_denied(self):
        ok, reason = sanitize_command("reboot", "sess-4")
        assert ok is False

    def test_cqr_write_denied(self):
        ok, reason = sanitize_command("echo hack > /cqr/inject-env.sh", "sess-5")
        assert ok is False

    def test_mkfs_denied(self):
        ok, reason = sanitize_command("mkfs.ext4 /dev/sda", "sess-6")
        assert ok is False

    def test_git_command_allowed(self):
        ok, reason = sanitize_command("git status", "sess-7")
        assert ok is True

    def test_pip_install_allowed(self):
        ok, reason = sanitize_command("pip install requests", "sess-8")
        assert ok is True

    def test_rm_rf_workspace_allowed(self):
        # rm -rf /workspace/subdir is allowed (not rm -rf /)
        ok, reason = sanitize_command("rm -rf /workspace/node_modules", "sess-9")
        assert ok is True


# ---------------------------------------------------------------------------
# apply_diff statistics
# ---------------------------------------------------------------------------


class TestApplyDiffStats:
    """Test that apply_diff correctly parses line statistics from a diff."""

    SAMPLE_DIFF = """\
--- a/src/main.py
+++ b/src/main.py
@@ -1,5 +1,7 @@
 import os
+import sys
+import json
 
 def main():
-    print("hello")
+    print("hello world")
+    return 0
"""

    def test_lines_added_counted(self):
        with patch("src.fs.operations._exec") as mock_exec:
            mock_exec.return_value = (0, "", "")
            result = apply_diff("fake-container", self.SAMPLE_DIFF, "task-001")
        assert result["lines_added"] == 4

    def test_lines_removed_counted(self):
        with patch("src.fs.operations._exec") as mock_exec:
            mock_exec.return_value = (0, "", "")
            result = apply_diff("fake-container", self.SAMPLE_DIFF, "task-001")
        assert result["lines_removed"] == 1

    def test_files_changed_counted(self):
        with patch("src.fs.operations._exec") as mock_exec:
            mock_exec.return_value = (0, "", "")
            result = apply_diff("fake-container", self.SAMPLE_DIFF, "task-001")
        assert result["files_changed"] == 1

    def test_failed_patch_returns_error_status(self):
        with patch("src.fs.operations._exec") as mock_exec:
            mock_exec.return_value = (1, "", "patch failed")
            result = apply_diff("fake-container", self.SAMPLE_DIFF, "task-001")
        assert result["status"] == "error"
        assert result["exit_code"] == 1


# ---------------------------------------------------------------------------
# Container manager (mocked Docker SDK)
# ---------------------------------------------------------------------------


class TestContainerManager:
    """Test container lifecycle operations with mocked Docker SDK."""

    def _make_mock_container(self, container_id="abc123", status="running"):
        c = MagicMock()
        c.id = container_id
        c.name = f"cqr-container-proj-1"
        c.status = status
        c.labels = {"cqr.project_id": "proj-1"}
        c.attrs = {
            "State": {"Running": status == "running", "StartedAt": "", "FinishedAt": "", "ExitCode": 0},
            "Config": {"Image": "cqr-sandbox:latest"},
        }
        return c

    @patch("src.containers.manager.docker.from_env")
    def test_start_container(self, mock_docker):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client
        mock_container = self._make_mock_container()
        mock_client.containers.get.return_value = mock_container

        from src.containers.manager import start_container
        result = start_container("abc123")

        mock_container.start.assert_called_once()
        assert result["status"] == "started"

    @patch("src.containers.manager.docker.from_env")
    def test_stop_container(self, mock_docker):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client
        mock_container = self._make_mock_container(status="running")
        mock_client.containers.get.return_value = mock_container

        from src.containers.manager import stop_container
        result = stop_container("abc123")

        mock_container.stop.assert_called_once_with(timeout=10)
        assert result["status"] == "stopped"

    @patch("src.containers.manager.docker.from_env")
    def test_restart_container(self, mock_docker):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client
        mock_container = self._make_mock_container()
        mock_client.containers.get.return_value = mock_container

        from src.containers.manager import restart_container
        result = restart_container("abc123")

        mock_container.restart.assert_called_once_with(timeout=10)
        assert result["status"] == "restarted"

    @patch("src.containers.manager.docker.from_env")
    def test_get_container_status(self, mock_docker):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client
        mock_container = self._make_mock_container(status="running")
        mock_client.containers.get.return_value = mock_container

        from src.containers.manager import get_container_status
        result = get_container_status("abc123")

        assert result["status"] == "running"
        assert result["running"] is True

    @patch("src.containers.manager.docker.from_env")
    def test_get_container_status_not_found(self, mock_docker):
        import docker.errors
        mock_client = MagicMock()
        mock_docker.return_value = mock_client
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        from src.containers.manager import get_container_status
        result = get_container_status("missing-id")

        assert result["status"] == "not_found"
        assert result["running"] is False

    @patch("src.containers.manager.docker.from_env")
    def test_remove_container(self, mock_docker):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client
        mock_container = self._make_mock_container()
        mock_client.containers.get.return_value = mock_container

        from src.containers.manager import remove_container
        result = remove_container("abc123", remove_volume=False)

        mock_container.remove.assert_called_once_with(force=True)
        assert result["status"] == "removed"


# ---------------------------------------------------------------------------
# Git operations (mocked _exec)
# ---------------------------------------------------------------------------


class TestGitOperations:
    @patch("src.fs.operations._exec")
    def test_git_status_clean(self, mock_exec):
        mock_exec.return_value = (0, "", "")
        from src.fs.operations import git_status
        result = git_status("container-1")
        assert result["clean"] is True
        assert result["changes"] == []

    @patch("src.fs.operations._exec")
    def test_git_status_with_changes(self, mock_exec):
        mock_exec.return_value = (0, " M src/main.py\n?? new_file.py\n", "")
        from src.fs.operations import git_status
        result = git_status("container-1")
        assert result["clean"] is False
        assert len(result["changes"]) == 2

    @patch("src.fs.operations._exec")
    def test_git_log_parses_commits(self, mock_exec):
        mock_exec.return_value = (0, "abc1234 fix: update main\ndef5678 feat: add feature\n", "")
        from src.fs.operations import git_log
        result = git_log("container-1", n=5)
        assert len(result["commits"]) == 2
        assert result["commits"][0]["sha"] == "abc1234"
        assert result["commits"][0]["message"] == "fix: update main"

    @patch("src.fs.operations._exec")
    def test_git_diff_returns_diff(self, mock_exec):
        sample_diff = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n"
        mock_exec.return_value = (0, sample_diff, "")
        from src.fs.operations import git_diff
        result = git_diff("container-1")
        assert result["diff"] == sample_diff
        assert result["exit_code"] == 0


# ---------------------------------------------------------------------------
# File operations (mocked _exec)
# ---------------------------------------------------------------------------


class TestFileOperations:
    @patch("src.fs.operations._exec")
    def test_write_file_success(self, mock_exec):
        mock_exec.return_value = (0, "", "")
        from src.fs.operations import write_file
        result = write_file("container-1", "src/hello.py", "print('hello')")
        assert result["ok"] is True
        assert "src/hello.py" in result["path"]

    @patch("src.fs.operations._exec")
    def test_read_file_success(self, mock_exec):
        mock_exec.return_value = (0, "print('hello')", "")
        from src.fs.operations import read_file
        result = read_file("container-1", "src/hello.py")
        assert result["content"] == "print('hello')"

    @patch("src.fs.operations._exec")
    def test_read_file_not_found(self, mock_exec):
        mock_exec.return_value = (1, "", "No such file or directory")
        from src.fs.operations import read_file
        result = read_file("container-1", "missing.py")
        assert result["content"] is None
        assert "No such file" in result["error"]

    @patch("src.fs.operations._exec")
    def test_list_directory(self, mock_exec):
        mock_exec.return_value = (0, "f\t1234\tmain.py\nd\t0\tsrc\n", "")
        from src.fs.operations import list_directory
        result = list_directory("container-1", "")
        assert len(result["entries"]) == 2
        assert result["entries"][0]["type"] == "file"
        assert result["entries"][1]["type"] == "dir"
