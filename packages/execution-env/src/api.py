"""
Execution Environment internal FastAPI router.
Manages container lifecycle, file operations, and shell execution.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .containers.manager import (
    create_container,
    get_container_status,
    start_container,
    stop_container,
)
from .fs.operations import (
    apply_diff,
    git_commit,
    git_diff,
    list_directory,
    read_file,
    write_file,
)
from .terminal.executor import run_command

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CQR Execution Environment",
    description="Sandboxed container management and file operations (internal).",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateContainerRequest(BaseModel):
    """Request body for POST /exec/container/create."""

    project_id: str
    repo_path: str


class RunCommandRequest(BaseModel):
    """Request body for POST /exec/run."""

    container_id: str
    command: str
    project_id: str
    session_id: str | None = None
    workdir: str = "/workspace"


class WriteFileRequest(BaseModel):
    """Request body for POST /exec/write-file."""

    container_id: str
    path: str
    content: str


class GitCommitRequest(BaseModel):
    """Request body for POST /exec/git/commit."""

    container_id: str
    message: str
    author: str = "CQR Agent"


class ApplyDiffRequest(BaseModel):
    """Request body for POST /exec/apply-diff."""

    project_id: str
    diff: str
    task_id: str
    container_id: str | None = None  # resolved from project_id if not provided


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Return service health status."""
    return {"status": "ok", "service": "execution-env"}


@app.post("/exec/container/create", tags=["containers"])
async def create(body: CreateContainerRequest) -> dict[str, Any]:
    """Provision a new isolated container for a project."""
    return create_container(body.project_id, body.repo_path)


@app.post("/exec/container/{container_id}/start", tags=["containers"])
async def start(container_id: str) -> dict[str, str]:
    """Start a stopped container."""
    return start_container(container_id)


@app.post("/exec/container/{container_id}/stop", tags=["containers"])
async def stop(container_id: str) -> dict[str, str]:
    """Gracefully stop a container."""
    return stop_container(container_id)


@app.get("/exec/container/{container_id}/status", tags=["containers"])
async def status(container_id: str) -> dict[str, str]:
    """Return the current status of a container."""
    return get_container_status(container_id)


@app.post("/exec/run", tags=["execution"])
async def run(body: RunCommandRequest) -> dict[str, Any]:
    """Run a shell command in a container. Command is sanitized before execution."""
    return run_command(
        container_id=body.container_id,
        command=body.command,
        project_id=body.project_id,
        session_id=body.session_id,
        workdir=body.workdir,
    )


@app.post("/exec/write-file", tags=["filesystem"])
async def write(body: WriteFileRequest) -> dict[str, Any]:
    """Write a file to the container /workspace path."""
    try:
        return write_file(body.container_id, body.path, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/exec/read-file", tags=["filesystem"])
async def read(
    container_id: str = Query(...),
    path: str = Query(...),
) -> dict[str, Any]:
    """Read a file from the container /workspace path."""
    try:
        return read_file(container_id, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/exec/ls", tags=["filesystem"])
async def ls(
    container_id: str = Query(...),
    path: str = Query(default=""),
) -> dict[str, Any]:
    """List directory contents in the container."""
    return list_directory(container_id, path)


@app.post("/exec/git/commit", tags=["git"])
async def commit(body: GitCommitRequest) -> dict[str, Any]:
    """Stage all changes and commit with an agent-authored message."""
    return git_commit(body.container_id, body.message, body.author)


@app.get("/exec/git/diff", tags=["git"])
async def diff(container_id: str = Query(...)) -> dict[str, Any]:
    """Return unified diff of uncommitted changes."""
    return git_diff(container_id)


@app.post("/exec/apply-diff", tags=["git"])
async def apply(body: ApplyDiffRequest) -> dict[str, Any]:
    """Apply a unified diff to the workspace."""
    if not body.container_id:
        raise HTTPException(status_code=400, detail="container_id is required")
    return apply_diff(body.container_id, body.diff, body.task_id)
