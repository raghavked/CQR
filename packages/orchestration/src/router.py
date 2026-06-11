"""
Orchestration API router — task routing and multi-package composition.
This is the only layer allowed to compose calls across multiple packages.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from .models import (
    AgentResponse,
    CreateProjectRequest,
    DispatchPayload,
    Project,
    ProjectResponse,
    SecurityReportResponse,
    SubmitTaskRequest,
    Task,
    TaskStatusResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Internal service base URLs — all resolved from environment variables
# ---------------------------------------------------------------------------

KG_ENGINE_URL = os.getenv("KG_ENGINE_URL", "http://localhost:8001")
LSM_LAYER_URL = os.getenv("LSM_LAYER_URL", "http://localhost:8002")
EXECUTION_ENV_URL = os.getenv("EXECUTION_ENV_URL", "http://localhost:8003")
VAULT_URL = os.getenv("VAULT_URL", "http://localhost:8004")
AGENT_BRIDGE_URL = os.getenv("AGENT_BRIDGE_URL", "http://localhost:8005")
SECURITY_SCANNER_URL = os.getenv("SECURITY_SCANNER_URL", "http://localhost:8006")

# ---------------------------------------------------------------------------
# In-memory stores (replace with DB in production)
# TODO(AMBIGUITY): Replace with persistent Postgres-backed store in CP-3
# ---------------------------------------------------------------------------
_projects: dict[str, Project] = {}
_tasks: dict[str, Task] = {}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _call_internal(
    method: str, url: str, **kwargs: Any
) -> dict:
    """Make an async HTTP call to an internal service."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()


# ---------------------------------------------------------------------------
# Project endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["projects"],
)
async def create_project(
    body: CreateProjectRequest, background_tasks: BackgroundTasks
) -> ProjectResponse:
    """Provision a new CQR project and its execution container."""
    project = Project(
        id=str(uuid.uuid4()),
        name=body.name,
        repo_path=body.repo_path,
        status="provisioning",
    )
    _projects[project.id] = project
    logger.info(
        '{"event": "project_created", "project_id": "%s", "name": "%s"}',
        project.id,
        project.name,
    )
    background_tasks.add_task(_provision_project, project)
    return ProjectResponse(project=project)


async def _provision_project(project: Project) -> None:
    """Background task: create container and trigger KG ingestion."""
    try:
        # 1. Create execution container
        container_data = await _call_internal(
            "POST",
            f"{EXECUTION_ENV_URL}/exec/container/create",
            json={"project_id": project.id, "repo_path": project.repo_path},
        )
        project.container_id = container_data.get("container_id")

        # 2. Start container
        await _call_internal(
            "POST",
            f"{EXECUTION_ENV_URL}/exec/container/{project.container_id}/start",
        )

        # 3. Trigger KG ingestion
        await _call_internal(
            "POST",
            f"{KG_ENGINE_URL}/kg/ingest",
            json={"project_id": project.id, "repo_path": project.repo_path},
        )

        project.status = "ready"
        logger.info(
            '{"event": "project_ready", "project_id": "%s"}', project.id
        )
    except Exception as exc:  # noqa: BLE001
        project.status = "error"
        logger.error(
            '{"event": "project_provision_error", "project_id": "%s", "error": "%s"}',
            project.id,
            str(exc),
        )


@router.get("/projects/{project_id}", response_model=ProjectResponse, tags=["projects"])
async def get_project(project_id: str) -> ProjectResponse:
    """Return project details by ID."""
    project = _projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(project=project)


@router.get("/projects", response_model=list[Project], tags=["projects"])
async def list_projects() -> list[Project]:
    """Return all projects."""
    return list(_projects.values())


# ---------------------------------------------------------------------------
# Task endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/tasks",
    response_model=TaskStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["tasks"],
)
async def submit_task(
    body: SubmitTaskRequest, background_tasks: BackgroundTasks
) -> TaskStatusResponse:
    """Submit an agent task for async processing."""
    project = _projects.get(body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != "ready":
        raise HTTPException(
            status_code=409, detail=f"Project is not ready (status={project.status})"
        )

    task = Task(
        id=str(uuid.uuid4()),
        project_id=body.project_id,
        description=body.description,
        agent=body.agent,
        budget_tier=body.budget_tier,
        status="queued",
    )
    _tasks[task.id] = task
    logger.info(
        '{"event": "task_queued", "task_id": "%s", "project_id": "%s"}',
        task.id,
        task.project_id,
    )
    background_tasks.add_task(_run_task, task)
    return TaskStatusResponse(task=task)


async def _run_task(task: Task) -> None:
    """Background task: assemble context, dispatch to agent, apply diff."""
    task.status = "running"
    try:
        # 1. Dispatch to Agent Bridge (context assembly + LLM call)
        payload = DispatchPayload(
            task_id=task.id,
            project_id=task.project_id,
            task_description=task.description,
            agent=task.agent,
            budget_tier=task.budget_tier,
        )
        agent_data = await _call_internal(
            "POST",
            f"{AGENT_BRIDGE_URL}/agent/dispatch",
            json=payload.model_dump(),
        )
        agent_response = AgentResponse(**agent_data)

        # 2. Apply diff to execution container
        if agent_response.diff:
            await _call_internal(
                "POST",
                f"{EXECUTION_ENV_URL}/exec/apply-diff",
                json={
                    "project_id": task.project_id,
                    "diff": agent_response.diff,
                    "task_id": task.id,
                },
            )

        # 3. Re-ingest changed files into KG
        await _call_internal(
            "POST",
            f"{KG_ENGINE_URL}/kg/ingest",
            json={"project_id": task.project_id, "repo_path": None},
        )

        # 4. Run security scan on modified nodes
        await _call_internal(
            "POST",
            f"{SECURITY_SCANNER_URL}/security/scan",
            json={"project_id": task.project_id, "task_id": task.id},
        )

        task.status = "done"
        task.completed_at = datetime.utcnow()
        task.diff = agent_response.diff
        task.confidence = agent_response.confidence
        task.token_usage = agent_response.token_usage
        logger.info(
            '{"event": "task_done", "task_id": "%s"}', task.id
        )
    except Exception as exc:  # noqa: BLE001
        task.status = "failed"
        task.completed_at = datetime.utcnow()
        logger.error(
            '{"event": "task_failed", "task_id": "%s", "error": "%s"}',
            task.id,
            str(exc),
        )


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse, tags=["tasks"])
async def get_task_status(task_id: str) -> TaskStatusResponse:
    """Return current status of a task."""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(task=task)


@router.get("/tasks", response_model=list[Task], tags=["tasks"])
async def list_tasks(project_id: str | None = None) -> list[Task]:
    """Return all tasks, optionally filtered by project."""
    tasks = list(_tasks.values())
    if project_id:
        tasks = [t for t in tasks if t.project_id == project_id]
    return tasks


# ---------------------------------------------------------------------------
# Security report endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/security/report/{project_id}",
    response_model=SecurityReportResponse,
    tags=["security"],
)
async def get_security_report(project_id: str) -> SecurityReportResponse:
    """Return the latest security findings for a project."""
    if project_id not in _projects:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        data = await _call_internal(
            "GET",
            f"{SECURITY_SCANNER_URL}/security/report/{project_id}",
        )
        return SecurityReportResponse(**data)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Security scanner error: {exc}") from exc
