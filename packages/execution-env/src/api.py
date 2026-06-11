"""
Execution Environment internal FastAPI router.

Manages container lifecycle, file operations, shell execution, and git operations.
This service is internal-only — all public access goes through the Orchestration API.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .containers.manager import (
    create_container,
    get_container_by_project,
    get_container_logs,
    get_container_status,
    remove_container,
    restart_container,
    start_container,
    stop_container,
)
from .fs.operations import (
    apply_diff,
    git_commit,
    git_diff,
    git_log,
    git_status,
    list_directory,
    read_file,
    write_file,
)
from .terminal.executor import run_command, stream_command

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CQR Execution Environment",
    description="Sandboxed container management and file operations (internal).",
    version="0.2.0",
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateContainerRequest(BaseModel):
    project_id: str
    repo_path: str


class RunCommandRequest(BaseModel):
    container_id: str
    command: str
    project_id: str
    session_id: str | None = None
    workdir: str = "/workspace"


class StreamCommandRequest(BaseModel):
    container_id: str
    command: str
    project_id: str
    session_id: str | None = None
    workdir: str = "/workspace"


class WriteFileRequest(BaseModel):
    container_id: str
    path: str
    content: str


class GitCommitRequest(BaseModel):
    container_id: str
    message: str
    author: str = "CQR Agent"


class ApplyDiffRequest(BaseModel):
    project_id: str
    diff: str
    task_id: str
    container_id: str | None = None


class RemoveContainerRequest(BaseModel):
    container_id: str
    remove_volume: bool = False


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "service": "execution-env", "version": "0.2.0"}


# ---------------------------------------------------------------------------
# Container lifecycle
# ---------------------------------------------------------------------------


@app.post("/exec/container/create", tags=["containers"])
async def create(body: CreateContainerRequest) -> dict[str, Any]:
    """Provision a new isolated container for a project."""
    try:
        return create_container(body.project_id, body.repo_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/exec/container/{container_id}/start", tags=["containers"])
async def start(container_id: str) -> dict[str, Any]:
    """Start a stopped container."""
    try:
        return start_container(container_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/exec/container/{container_id}/stop", tags=["containers"])
async def stop(container_id: str) -> dict[str, Any]:
    """Gracefully stop a container."""
    try:
        return stop_container(container_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/exec/container/{container_id}/restart", tags=["containers"])
async def restart(container_id: str) -> dict[str, Any]:
    """Restart a container (stop + start)."""
    try:
        return restart_container(container_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/exec/container/{container_id}", tags=["containers"])
async def remove(container_id: str, remove_volume: bool = Query(default=False)) -> dict[str, Any]:
    """Remove a container. Pass remove_volume=true to also delete the workspace volume."""
    return remove_container(container_id, remove_volume=remove_volume)


@app.get("/exec/container/{container_id}/status", tags=["containers"])
async def status(container_id: str) -> dict[str, Any]:
    """Return the current status of a container."""
    return get_container_status(container_id)


@app.get("/exec/container/{container_id}/logs", tags=["containers"])
async def logs(container_id: str, tail: int = Query(default=100)) -> dict[str, Any]:
    """Return the last N lines of container logs."""
    return get_container_logs(container_id, tail=tail)


@app.get("/exec/container/by-project/{project_id}", tags=["containers"])
async def by_project(project_id: str) -> dict[str, Any]:
    """Look up container status by project ID label."""
    result = get_container_by_project(project_id)
    if not result:
        raise HTTPException(status_code=404, detail="No container found for project")
    return result


# ---------------------------------------------------------------------------
# Shell execution
# ---------------------------------------------------------------------------


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


@app.post("/exec/stream", tags=["execution"])
async def stream(body: StreamCommandRequest) -> StreamingResponse:
    """
    Stream shell command stdout/stderr as NDJSON chunks.
    Each line is a JSON object: {type: 'stdout'|'stderr'|'exit', data: str, session_id: str}
    """
    import json

    async def _generate():
        async for chunk in stream_command(
            container_id=body.container_id,
            command=body.command,
            project_id=body.project_id,
            session_id=body.session_id,
            workdir=body.workdir,
        ):
            yield json.dumps(chunk) + "\n"

    return StreamingResponse(_generate(), media_type="application/x-ndjson")


# ---------------------------------------------------------------------------
# File system operations
# ---------------------------------------------------------------------------


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
    """List directory contents in the container with type and size info."""
    try:
        return list_directory(container_id, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------


@app.get("/exec/git/status", tags=["git"])
async def git_status_endpoint(container_id: str = Query(...)) -> dict[str, Any]:
    """Return git status (porcelain) of the workspace."""
    return git_status(container_id)


@app.get("/exec/git/diff", tags=["git"])
async def diff(
    container_id: str = Query(...),
    staged: bool = Query(default=False),
) -> dict[str, Any]:
    """Return unified diff of uncommitted changes (or staged changes if staged=true)."""
    return git_diff(container_id, staged=staged)


@app.get("/exec/git/log", tags=["git"])
async def log(
    container_id: str = Query(...),
    n: int = Query(default=10),
) -> dict[str, Any]:
    """Return the last N commits in the workspace."""
    return git_log(container_id, n=n)


@app.post("/exec/git/commit", tags=["git"])
async def commit(body: GitCommitRequest) -> dict[str, Any]:
    """Stage all changes and commit with an agent-authored message."""
    return git_commit(body.container_id, body.message, body.author)


@app.post("/exec/apply-diff", tags=["git"])
async def apply(body: ApplyDiffRequest) -> dict[str, Any]:
    """Apply a unified diff to the workspace."""
    container_id = body.container_id
    if not container_id:
        # Resolve from project_id
        from .containers.manager import get_container_by_project
        info = get_container_by_project(body.project_id)
        container_id = info.get("container_id")
        if not container_id:
            raise HTTPException(
                status_code=404,
                detail=f"No running container found for project {body.project_id}",
            )
    return apply_diff(container_id, body.diff, body.task_id)
