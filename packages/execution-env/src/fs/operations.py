"""
File system and git operations inside project containers.

Provides read, write, list, and git operations via Docker exec.
All paths are validated against /workspace to prevent traversal attacks.
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import PurePosixPath
from typing import Any

import docker
import docker.errors

logger = logging.getLogger(__name__)

_WORKSPACE = "/workspace"


def _exec(
    container_id: str,
    cmd: list[str],
    workdir: str = _WORKSPACE,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a command in a container and return (exit_code, stdout, stderr)."""
    client = docker.from_env()
    container = client.containers.get(container_id)
    kwargs: dict[str, Any] = {"cmd": cmd, "workdir": workdir, "demux": True}
    if env:
        kwargs["environment"] = env
    exit_code, output = container.exec_run(**kwargs)
    stdout_bytes, stderr_bytes = output if output else (b"", b"")
    return (
        exit_code,
        (stdout_bytes or b"").decode("utf-8", errors="replace"),
        (stderr_bytes or b"").decode("utf-8", errors="replace"),
    )


def _safe_workspace_path(path: str) -> str:
    """
    Ensure a path is safely rooted under /workspace.
    Prevents path traversal attacks (e.g. ../../../etc/passwd).
    Raises ValueError if the resolved path escapes /workspace.
    """
    # Reject empty path — return workspace root
    if not path:
        return _WORKSPACE

    # Reject absolute paths that don't start with /workspace
    if path.startswith("/") and not path.startswith(_WORKSPACE):
        raise ValueError(f"Path traversal detected: {path!r} resolves outside /workspace")

    # Strip leading slashes so we can join cleanly
    clean = path.lstrip("/")

    # Reject any path containing .. components before joining
    parts = PurePosixPath(clean).parts
    if ".." in parts:
        raise ValueError(f"Path traversal detected: {path!r} resolves outside /workspace")

    # Resolve against /workspace using PurePosixPath (no filesystem access)
    resolved = PurePosixPath(_WORKSPACE) / clean

    # Normalise away any remaining .. components via string manipulation
    # (PurePosixPath does not call os.path.realpath, so no filesystem access)
    normalised_parts: list[str] = []
    for part in resolved.parts:
        if part == "..":
            if normalised_parts and normalised_parts[-1] != "/":
                normalised_parts.pop()
        else:
            normalised_parts.append(part)
    normalised = str(PurePosixPath(*normalised_parts)) if normalised_parts else _WORKSPACE

    # Final guard
    if not normalised.startswith(_WORKSPACE):
        raise ValueError(f"Path traversal detected: {path!r} resolves outside /workspace")

    return str(resolved)


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------


def write_file(container_id: str, path: str, content: str) -> dict[str, Any]:
    """
    Write content to a file at the given path inside /workspace.
    Path must be relative to /workspace (or an absolute /workspace/... path).
    """
    safe_path = _safe_workspace_path(path)
    # Encode content as base64 to avoid shell escaping issues
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    dir_path = os.path.dirname(safe_path)
    cmd = [
        "bash", "-c",
        f"mkdir -p {dir_path} && printf '%s' '{encoded}' | base64 -d > {safe_path}",
    ]
    exit_code, stdout, stderr = _exec(container_id, cmd)
    return {"path": safe_path, "exit_code": exit_code, "stderr": stderr, "ok": exit_code == 0}


def read_file(container_id: str, path: str) -> dict[str, Any]:
    """Read a file from /workspace in the container."""
    safe_path = _safe_workspace_path(path)
    exit_code, stdout, stderr = _exec(container_id, ["cat", safe_path])
    if exit_code != 0:
        return {"path": safe_path, "content": None, "error": stderr}
    return {"path": safe_path, "content": stdout}


def list_directory(container_id: str, path: str = "") -> dict[str, Any]:
    """List directory contents inside /workspace with type info."""
    safe_path = _safe_workspace_path(path) if path else _WORKSPACE
    # Use find for structured output: type, size, name
    cmd = [
        "bash", "-c",
        f"find {safe_path} -maxdepth 1 -printf '%y\\t%s\\t%P\\n' 2>/dev/null | tail -n +2",
    ]
    exit_code, stdout, stderr = _exec(container_id, cmd)
    entries = []
    for line in stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            ftype, size, name = parts
            entries.append({
                "name": name,
                "type": "dir" if ftype == "d" else "file",
                "size": int(size) if size.isdigit() else 0,
            })
    return {"path": safe_path, "entries": entries, "exit_code": exit_code}


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "CQR Agent",
    "GIT_AUTHOR_EMAIL": "agent@cqr.dev",
    "GIT_COMMITTER_NAME": "CQR Agent",
    "GIT_COMMITTER_EMAIL": "agent@cqr.dev",
}


def git_status(container_id: str) -> dict[str, Any]:
    """Return the current git status (porcelain format) of /workspace."""
    exit_code, stdout, stderr = _exec(
        container_id, ["git", "status", "--porcelain"], env=_GIT_ENV
    )
    changes = []
    for line in stdout.splitlines():
        if len(line) >= 3:
            xy = line[:2]
            fname = line[3:]
            changes.append({"status": xy.strip(), "file": fname})
    return {
        "changes": changes,
        "clean": len(changes) == 0,
        "exit_code": exit_code,
        "stderr": stderr,
    }


def git_diff(container_id: str, staged: bool = False) -> dict[str, Any]:
    """Return unified diff of uncommitted (or staged) changes."""
    cmd = ["git", "diff"]
    if staged:
        cmd.append("--cached")
    else:
        cmd.extend(["HEAD", "--"])
    exit_code, stdout, stderr = _exec(container_id, cmd, env=_GIT_ENV)
    return {"diff": stdout, "exit_code": exit_code, "stderr": stderr}


def git_log(container_id: str, n: int = 10) -> dict[str, Any]:
    """Return the last N commits in the workspace."""
    exit_code, stdout, stderr = _exec(
        container_id,
        ["git", "log", f"-{n}", "--oneline", "--no-decorate"],
        env=_GIT_ENV,
    )
    commits = []
    for line in stdout.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2:
            commits.append({"sha": parts[0], "message": parts[1]})
    return {"commits": commits, "exit_code": exit_code}


def git_commit(container_id: str, message: str, author: str = "CQR Agent") -> dict[str, Any]:
    """Stage all changes and commit with the given message."""
    env = {
        **_GIT_ENV,
        "GIT_AUTHOR_NAME": author,
        "GIT_COMMITTER_NAME": author,
    }

    # Ensure git is initialised
    _exec(container_id, ["git", "init"], env=env)
    _exec(container_id, ["git", "config", "user.email", "agent@cqr.dev"], env=env)
    _exec(container_id, ["git", "config", "user.name", author], env=env)

    # Stage all changes
    stage_code, _, stage_err = _exec(container_id, ["git", "add", "-A"], env=env)
    if stage_code != 0:
        return {"status": "error", "error": stage_err}

    # Commit
    commit_code, commit_out, commit_err = _exec(
        container_id, ["git", "commit", "-m", message], env=env
    )
    return {
        "status": "ok" if commit_code == 0 else "error",
        "output": commit_out,
        "error": commit_err,
    }


def apply_diff(container_id: str, diff: str, task_id: str) -> dict[str, Any]:
    """
    Apply a unified diff to the workspace using the `patch` command.
    Returns statistics: files_changed, lines_added, lines_removed.
    """
    # Write diff to a temp file inside the container
    encoded = base64.b64encode(diff.encode("utf-8")).decode("ascii")
    tmp_path = f"/tmp/agent-scratch/{task_id}.patch"
    write_cmd = [
        "bash", "-c",
        f"mkdir -p /tmp/agent-scratch && printf '%s' '{encoded}' | base64 -d > {tmp_path}",
    ]
    _exec(container_id, write_cmd)

    # Apply the patch
    exit_code, stdout, stderr = _exec(
        container_id, ["patch", "-p1", "--input", tmp_path]
    )

    # Parse statistics from diff header
    # Count '--- a/...' lines (each represents one changed file)
    files_changed = sum(
        1 for line in diff.splitlines() if line.startswith("--- ") and not line.startswith("--- /dev/null")
    )
    lines_added = sum(
        1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    lines_removed = sum(
        1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")
    )

    return {
        "status": "ok" if exit_code == 0 else "error",
        "output": stdout,
        "error": stderr,
        "exit_code": exit_code,
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
    }
