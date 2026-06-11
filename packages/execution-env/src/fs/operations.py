"""
File system operations inside project containers.
Provides read, write, list, and git operations via Docker exec.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any

import docker
import docker.errors

logger = logging.getLogger(__name__)

_WORKSPACE = "/workspace"


def _exec(container_id: str, cmd: list[str], workdir: str = _WORKSPACE) -> tuple[int, str, str]:
    """Run a command in a container and return (exit_code, stdout, stderr)."""
    client = docker.from_env()
    container = client.containers.get(container_id)
    exit_code, output = container.exec_run(cmd=cmd, workdir=workdir, demux=True)
    stdout_bytes, stderr_bytes = output if output else (b"", b"")
    return (
        exit_code,
        (stdout_bytes or b"").decode("utf-8", errors="replace"),
        (stderr_bytes or b"").decode("utf-8", errors="replace"),
    )


def write_file(container_id: str, path: str, content: str) -> dict[str, Any]:
    """
    Write content to a file at the given path inside /workspace.
    Path must be relative to /workspace.
    """
    safe_path = _safe_workspace_path(path)
    # Encode content as base64 to avoid shell escaping issues
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    cmd = ["bash", "-c", f"mkdir -p $(dirname {safe_path}) && echo '{encoded}' | base64 -d > {safe_path}"]
    exit_code, stdout, stderr = _exec(container_id, cmd)
    return {"path": safe_path, "exit_code": exit_code, "stderr": stderr}


def read_file(container_id: str, path: str) -> dict[str, Any]:
    """Read a file from /workspace in the container."""
    safe_path = _safe_workspace_path(path)
    exit_code, stdout, stderr = _exec(container_id, ["cat", safe_path])
    if exit_code != 0:
        return {"path": safe_path, "content": None, "error": stderr}
    return {"path": safe_path, "content": stdout}


def list_directory(container_id: str, path: str = "") -> dict[str, Any]:
    """List directory contents inside /workspace."""
    safe_path = _safe_workspace_path(path) if path else _WORKSPACE
    exit_code, stdout, stderr = _exec(container_id, ["ls", "-la", safe_path])
    entries = [line for line in stdout.splitlines() if line]
    return {"path": safe_path, "entries": entries, "exit_code": exit_code}


def git_commit(container_id: str, message: str, author: str = "CQR Agent") -> dict[str, Any]:
    """Stage all changes and commit with the given message."""
    # Configure git identity for this container session
    _exec(container_id, ["git", "config", "user.email", "agent@cqr.dev"])
    _exec(container_id, ["git", "config", "user.name", author])

    # Stage all changes
    stage_code, _, stage_err = _exec(container_id, ["git", "add", "-A"])
    if stage_code != 0:
        return {"status": "error", "error": stage_err}

    # Commit
    commit_code, commit_out, commit_err = _exec(
        container_id, ["git", "commit", "-m", message]
    )
    return {
        "status": "ok" if commit_code == 0 else "error",
        "output": commit_out,
        "error": commit_err,
    }


def git_diff(container_id: str) -> dict[str, Any]:
    """Return unified diff of uncommitted changes."""
    exit_code, stdout, stderr = _exec(container_id, ["git", "diff", "HEAD"])
    return {"diff": stdout, "exit_code": exit_code}


def apply_diff(container_id: str, diff: str, task_id: str) -> dict[str, Any]:
    """Apply a unified diff to the workspace using the patch command."""
    # Write diff to a temp file inside the container
    encoded = base64.b64encode(diff.encode("utf-8")).decode("ascii")
    tmp_path = f"/tmp/agent-scratch/{task_id}.patch"
    write_cmd = ["bash", "-c", f"mkdir -p /tmp/agent-scratch && echo '{encoded}' | base64 -d > {tmp_path}"]
    _exec(container_id, write_cmd)

    # Apply the patch
    exit_code, stdout, stderr = _exec(
        container_id, ["patch", "-p1", "--input", tmp_path]
    )
    return {
        "status": "ok" if exit_code == 0 else "error",
        "output": stdout,
        "error": stderr,
        "exit_code": exit_code,
    }


def _safe_workspace_path(path: str) -> str:
    """
    Ensure a path is safely rooted under /workspace.
    Prevents path traversal attacks.
    """
    # Strip leading slashes and resolve
    clean = path.lstrip("/")
    # Block traversal attempts
    if ".." in clean.split("/"):
        raise ValueError(f"Path traversal detected in: {path}")
    return os.path.join(_WORKSPACE, clean)
