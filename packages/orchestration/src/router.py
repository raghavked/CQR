"""
Orchestration API router — task routing and multi-package composition.

This is the only layer allowed to compose calls across multiple packages.
All project and task state is persisted in Postgres via db.py.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from .db import (
    create_project,
    create_task,
    get_project,
    get_task,
    list_projects,
    list_tasks,
    update_project,
    update_task,
)
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
    TokenUsage,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Internal service base URLs
# ---------------------------------------------------------------------------

KG_ENGINE_URL = os.getenv("KG_ENGINE_URL", "http://localhost:8001")
LSM_LAYER_URL = os.getenv("LSM_LAYER_URL", "http://localhost:8002")
EXECUTION_ENV_URL = os.getenv("EXECUTION_ENV_URL", "http://localhost:8003")
VAULT_URL = os.getenv("VAULT_URL", "http://localhost:8004")
AGENT_BRIDGE_URL = os.getenv("AGENT_BRIDGE_URL", "http://localhost:8005")
SECURITY_SCANNER_URL = os.getenv("SECURITY_SCANNER_URL", "http://localhost:8006")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _call_internal(method: str, url: str, **kwargs: Any) -> dict:
    """Make an async HTTP call to an internal service."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()


def _row_to_project(row: dict) -> Project:
    """Convert a Postgres row dict to a Project model."""
    return Project(
        id=str(row["id"]),
        name=row["name"],
        repo_path=row.get("repo_path") or "",
        container_id=row.get("container_id"),
        status=row.get("status", "provisioning"),
        created_at=row.get("created_at", datetime.utcnow()),
    )


def _row_to_task(row: dict) -> Task:
    """Convert a Postgres row dict to a Task model."""
    token_usage = None
    if row.get("token_usage"):
        raw = row["token_usage"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        token_usage = TokenUsage(**raw)

    return Task(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        description=row.get("description") or "",
        agent=row.get("agent", "claude"),
        budget_tier=row.get("budget_tier", "standard"),
        status=row.get("status", "queued"),
        created_at=row.get("created_at", datetime.utcnow()),
        completed_at=row.get("completed_at"),
        token_usage=token_usage,
        diff=row.get("diff"),
        confidence=row.get("confidence"),
    )


# ---------------------------------------------------------------------------
# Project endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["projects"],
)
async def create_project_endpoint(
    body: CreateProjectRequest, background_tasks: BackgroundTasks
) -> ProjectResponse:
    """Provision a new CQR project and its execution container."""
    project_id = str(uuid.uuid4())
    row = await create_project(
        project_id=project_id,
        name=body.name,
        repo_path=body.repo_path,
        status="provisioning",
    )
    project = _row_to_project(row)
    logger.info(
        '{"event": "project_created", "project_id": "%s", "name": "%s"}',
        project.id,
        project.name,
    )
    background_tasks.add_task(_provision_project, project_id)
    return ProjectResponse(project=project)


async def _provision_project(project_id: str) -> None:
    """Background task: create container and trigger KG ingestion."""
    row = await get_project(project_id)
    if not row:
        return
    repo_path = row.get("repo_path") or ""
    try:
        # 1. Create execution container
        container_data = await _call_internal(
            "POST",
            f"{EXECUTION_ENV_URL}/exec/container/create",
            json={"project_id": project_id, "repo_path": repo_path},
        )
        container_id = container_data.get("container_id")
        await update_project(project_id, container_id=container_id)

        # 2. Start container
        await _call_internal(
            "POST",
            f"{EXECUTION_ENV_URL}/exec/container/{container_id}/start",
        )

        # 3. Trigger KG ingestion
        await _call_internal(
            "POST",
            f"{KG_ENGINE_URL}/kg/ingest",
            json={"project_id": project_id, "repo_path": repo_path},
        )

        await update_project(project_id, status="ready")
        logger.info('{"event": "project_ready", "project_id": "%s"}', project_id)
    except Exception as exc:  # noqa: BLE001
        await update_project(project_id, status="error")
        logger.error(
            '{"event": "project_provision_error", "project_id": "%s", "error": "%s"}',
            project_id,
            str(exc),
        )


@router.get("/projects/{project_id}", response_model=ProjectResponse, tags=["projects"])
async def get_project_endpoint(project_id: str) -> ProjectResponse:
    """Return project details by ID."""
    row = await get_project(project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(project=_row_to_project(row))


@router.get("/projects", response_model=list[Project], tags=["projects"])
async def list_projects_endpoint() -> list[Project]:
    """Return all projects."""
    rows = await list_projects()
    return [_row_to_project(r) for r in rows]


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
    project_row = await get_project(body.project_id)
    if not project_row:
        raise HTTPException(status_code=404, detail="Project not found")
    if project_row.get("status") != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Project is not ready (status={project_row.get('status')})",
        )

    task_id = str(uuid.uuid4())
    row = await create_task(
        task_id=task_id,
        project_id=body.project_id,
        description=body.description,
        agent=body.agent,
        budget_tier=body.budget_tier,
        status="queued",
    )
    task = _row_to_task(row)
    logger.info(
        '{"event": "task_queued", "task_id": "%s", "project_id": "%s"}',
        task.id,
        task.project_id,
    )
    # api_key is forwarded at call time only — never stored on the task row
    background_tasks.add_task(_run_task, task_id, body.api_key, body.api_key_type)
    return TaskStatusResponse(task=task)


async def _run_task(
    task_id: str,
    api_key: str | None = None,
    api_key_type: str | None = None,
) -> None:
    """
    Background task: assemble context, dispatch to agent, apply diff.
    api_key is accepted here but NEVER written to the database or logged.
    It flows only into the DispatchPayload and is destroyed after the call.
    """
    await update_task(task_id, status="running")
    row = await get_task(task_id)
    if not row:
        return

    try:
        # 1. Dispatch to Agent Bridge (context assembly + LLM call)
        payload = DispatchPayload(
            task_id=task_id,
            project_id=str(row["project_id"]),
            task_description=row.get("description") or "",
            agent=row.get("agent", "claude"),
            budget_tier=row.get("budget_tier", "standard"),
            api_key=api_key,
            api_key_type=api_key_type,
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
                    "project_id": str(row["project_id"]),
                    "diff": agent_response.diff,
                    "task_id": task_id,
                },
            )

        # 3. Re-ingest changed files into KG
        await _call_internal(
            "POST",
            f"{KG_ENGINE_URL}/kg/ingest",
            json={"project_id": str(row["project_id"]), "repo_path": None},
        )

        # 4. Run security scan on modified nodes
        await _call_internal(
            "POST",
            f"{SECURITY_SCANNER_URL}/security/scan",
            json={"project_id": str(row["project_id"]), "task_id": task_id},
        )

        # 5. Persist results — api_key is NOT included
        token_usage_json = (
            agent_response.token_usage.model_dump() if agent_response.token_usage else None
        )
        await update_task(
            task_id,
            status="done",
            completed_at=datetime.utcnow(),
            diff=agent_response.diff,
            confidence=agent_response.confidence,
            token_usage=json.dumps(token_usage_json) if token_usage_json else None,
        )
        logger.info('{"event": "task_done", "task_id": "%s"}', task_id)

    except Exception as exc:  # noqa: BLE001
        await update_task(task_id, status="failed", completed_at=datetime.utcnow())
        logger.error(
            '{"event": "task_failed", "task_id": "%s", "error": "%s"}',
            task_id,
            str(exc),
        )


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse, tags=["tasks"])
async def get_task_status(task_id: str) -> TaskStatusResponse:
    """Return current status of a task."""
    row = await get_task(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(task=_row_to_task(row))


@router.get("/tasks", response_model=list[Task], tags=["tasks"])
async def list_tasks_endpoint(project_id: str | None = None) -> list[Task]:
    """Return all tasks, optionally filtered by project."""
    rows = await list_tasks(project_id)
    return [_row_to_task(r) for r in rows]


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
    row = await get_project(project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        data = await _call_internal(
            "GET",
            f"{SECURITY_SCANNER_URL}/security/report/{project_id}",
        )
        return SecurityReportResponse(**data)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Security scanner error: {exc}") from exc
