"""
Orchestration API router — task routing and multi-package composition.

This is the only layer allowed to compose calls across multiple packages.
All project and task state is persisted in Postgres via db.py.
WebSocket events are emitted at every stage via ws.py.

PDR §9.1 REST endpoints implemented here:
  POST   /api/projects                     — create project
  GET    /api/projects/{id}                — project metadata + container health
  POST   /api/projects/{id}/ingest         — re-ingest project directory into KG
  POST   /api/tasks                        — submit task (async, returns task_id)
  GET    /api/tasks/{id}                   — task status, result, token usage
  GET    /api/tasks/{id}/diff              — unified diff produced by agent
  POST   /api/tasks/{id}/apply             — apply agent diff to container
  POST   /api/tasks/{id}/reject            — reject agent diff (log reason)
  GET    /api/kg/explore                   — passthrough to KG subgraph
  GET    /api/security/report/{project_id} — latest security scan results
  POST   /api/vault/keys                   — store a new secret
  GET    /api/vault/keys/{project_id}      — list key names for a project
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

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
from .ws import (
    emit_container_status,
    emit_kg_updated,
    emit_security_alert,
    emit_task_applied,
    emit_task_context_assembled,
    emit_task_diff_ready,
    emit_task_failed,
    emit_task_started,
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
# Helpers
# ---------------------------------------------------------------------------


async def _call_internal(method: str, url: str, **kwargs: Any) -> dict:
    """Make an async HTTP call to an internal service."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()


def _row_to_project(row: dict) -> Project:
    return Project(
        id=str(row["id"]),
        name=row["name"],
        repo_path=row.get("repo_path") or "",
        container_id=row.get("container_id"),
        status=row.get("status", "provisioning"),
        created_at=row.get("created_at", datetime.utcnow()),
    )


def _row_to_task(row: dict) -> Task:
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
    """Background task: create container, start it, and trigger KG ingestion."""
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
        await emit_container_status(project_id, "running", container_id)

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
        await emit_container_status(project_id, "error", "")
        logger.error(
            '{"event": "project_provision_error", "project_id": "%s", "error": "%s"}',
            project_id,
            str(exc),
        )


@router.get("/projects/{project_id}", response_model=ProjectResponse, tags=["projects"])
async def get_project_endpoint(project_id: str) -> ProjectResponse:
    """Return project metadata, status, and container health."""
    row = await get_project(project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    project = _row_to_project(row)

    # Enrich with live container health if container_id is known
    if project.container_id:
        try:
            container_info = await _call_internal(
                "GET",
                f"{EXECUTION_ENV_URL}/exec/container/{project.container_id}/status",
            )
            # Reflect live container state in project status
            if container_info.get("status") == "running" and project.status == "ready":
                pass  # already correct
            elif container_info.get("status") == "exited":
                project.status = "stopped"
        except Exception:  # noqa: BLE001
            pass  # container service unavailable — return DB state

    return ProjectResponse(project=project)


@router.get("/projects", response_model=list[Project], tags=["projects"])
async def list_projects_endpoint() -> list[Project]:
    """Return all projects."""
    rows = await list_projects()
    return [_row_to_project(r) for r in rows]


@router.post("/projects/{project_id}/ingest", tags=["projects"])
async def ingest_project(project_id: str) -> dict[str, Any]:
    """Re-ingest the project directory into the KG."""
    row = await get_project(project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    repo_path = row.get("repo_path") or ""
    try:
        result = await _call_internal(
            "POST",
            f"{KG_ENGINE_URL}/kg/ingest",
            json={"project_id": project_id, "repo_path": repo_path},
        )
        node_ids = result.get("summary", {}).get("changed_node_ids", [])
        await emit_kg_updated(project_id, node_ids)
        return result
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"KG engine error: {exc}") from exc


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
    """Submit an agent task for async processing. Returns task_id immediately."""
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
    background_tasks.add_task(_run_task, task_id, body.api_key, body.api_key_type)
    return TaskStatusResponse(task=task)


async def _run_task(
    task_id: str,
    api_key: str | None = None,
    api_key_type: str | None = None,
) -> None:
    """
    Background task: assemble context → dispatch to agent → apply diff → re-ingest → scan.
    Emits WebSocket events at every stage.
    api_key is NEVER written to the database or logged.
    """
    row = await get_task(task_id)
    if not row:
        return
    project_id = str(row["project_id"])

    await update_task(task_id, status="running")

    # Stage 1: task.started
    await emit_task_started(
        project_id, task_id,
        agent=row.get("agent", "claude"),
        budget_tier=row.get("budget_tier", "standard"),
    )

    try:
        # Stage 2: dispatch to Agent Bridge (context assembly + LLM call)
        payload = DispatchPayload(
            task_id=task_id,
            project_id=project_id,
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

        # Stage 3: task.context_assembled (from token_usage if available)
        if agent_response.token_usage:
            await emit_task_context_assembled(
                project_id, task_id,
                token_count=agent_response.token_usage.prompt_tokens or 0,
                node_count=agent_response.token_usage.context_node_count or 0,
            )

        # Stage 4: task.diff_ready
        if agent_response.diff:
            await emit_task_diff_ready(
                project_id, task_id,
                diff=agent_response.diff,
                confidence=agent_response.confidence or 0.0,
                token_usage=agent_response.token_usage.model_dump()
                if agent_response.token_usage else {},
            )

        # Stage 5: apply diff to execution container
        apply_result: dict[str, Any] = {}
        if agent_response.diff:
            project_row = await get_project(project_id)
            container_id = project_row.get("container_id") if project_row else None
            apply_result = await _call_internal(
                "POST",
                f"{EXECUTION_ENV_URL}/exec/apply-diff",
                json={
                    "project_id": project_id,
                    "diff": agent_response.diff,
                    "task_id": task_id,
                    "container_id": container_id,
                },
            )
            await emit_task_applied(
                project_id, task_id,
                files_changed=apply_result.get("files_changed", 0),
                lines_added=apply_result.get("lines_added", 0),
                lines_removed=apply_result.get("lines_removed", 0),
            )

        # Stage 6: re-ingest changed files into KG
        ingest_result = await _call_internal(
            "POST",
            f"{KG_ENGINE_URL}/kg/ingest",
            json={"project_id": project_id, "repo_path": None},
        )
        node_ids_changed = ingest_result.get("summary", {}).get("changed_node_ids", [])
        await emit_kg_updated(project_id, node_ids_changed)

        # Stage 7: run security scan on modified nodes
        scan_result = await _call_internal(
            "POST",
            f"{SECURITY_SCANNER_URL}/security/scan",
            json={"project_id": project_id, "task_id": task_id},
        )
        # Emit security.alert for every HIGH or CRITICAL finding
        for finding in scan_result.get("findings", []):
            if finding.get("severity", "").lower() in ("high", "critical"):
                await emit_security_alert(
                    project_id,
                    severity=finding["severity"],
                    pattern=finding.get("pattern", ""),
                    node_ids=finding.get("node_path", []),
                    task_id=task_id,
                )

        # Stage 8: persist results (api_key is NOT included)
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
        await emit_task_failed(
            project_id, task_id,
            error=str(exc),
            stage="dispatch",
            recoverable=False,
        )
        logger.error(
            '{"event": "task_failed", "task_id": "%s", "error": "%s"}',
            task_id,
            str(exc),
        )


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse, tags=["tasks"])
async def get_task_status(task_id: str) -> TaskStatusResponse:
    """Return current status, result, and token usage of a task."""
    row = await get_task(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(task=_row_to_task(row))


@router.get("/tasks/{task_id}/diff", tags=["tasks"])
async def get_task_diff(task_id: str) -> dict[str, Any]:
    """Return the unified diff produced by the agent for this task."""
    row = await get_task(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    if not row.get("diff"):
        raise HTTPException(status_code=404, detail="No diff available for this task")
    return {"task_id": task_id, "diff": row["diff"]}


@router.post("/tasks/{task_id}/apply", tags=["tasks"])
async def apply_task_diff(task_id: str) -> dict[str, Any]:
    """Apply the agent diff for a task to the container file system."""
    row = await get_task(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    if not row.get("diff"):
        raise HTTPException(status_code=404, detail="No diff available for this task")

    project_id = str(row["project_id"])
    project_row = await get_project(project_id)
    container_id = project_row.get("container_id") if project_row else None

    try:
        result = await _call_internal(
            "POST",
            f"{EXECUTION_ENV_URL}/exec/apply-diff",
            json={
                "project_id": project_id,
                "diff": row["diff"],
                "task_id": task_id,
                "container_id": container_id,
            },
        )
        await emit_task_applied(
            project_id, task_id,
            files_changed=result.get("files_changed", 0),
            lines_added=result.get("lines_added", 0),
            lines_removed=result.get("lines_removed", 0),
        )
        return result
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Execution env error: {exc}") from exc


@router.post("/tasks/{task_id}/reject", tags=["tasks"])
async def reject_task_diff(task_id: str, reason: str = Query(default="")) -> dict[str, Any]:
    """Reject the agent diff for a task (no-op on files, logs rejection reason)."""
    row = await get_task(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    await update_task(task_id, status="rejected", completed_at=datetime.utcnow())
    logger.info(
        '{"event": "task_rejected", "task_id": "%s", "reason": "%s"}',
        task_id,
        reason[:500],
    )
    return {"task_id": task_id, "status": "rejected", "reason": reason}


@router.get("/tasks", response_model=list[Task], tags=["tasks"])
async def list_tasks_endpoint(project_id: str | None = None) -> list[Task]:
    """Return all tasks, optionally filtered by project."""
    rows = await list_tasks(project_id)
    return [_row_to_task(r) for r in rows]


# ---------------------------------------------------------------------------
# KG explore passthrough
# ---------------------------------------------------------------------------


@router.get("/kg/explore", tags=["kg"])
async def kg_explore(
    project_id: str = Query(...),
    node_id: str | None = Query(default=None),
    depth: int = Query(default=2),
) -> dict[str, Any]:
    """Public passthrough to KG subgraph (for frontend KG Explorer)."""
    params: dict[str, Any] = {"project_id": project_id, "depth": depth}
    if node_id:
        params["node_id"] = node_id
    try:
        return await _call_internal(
            "GET",
            f"{KG_ENGINE_URL}/kg/subgraph",
            params=params,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"KG engine error: {exc}") from exc


# ---------------------------------------------------------------------------
# Security report
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


# ---------------------------------------------------------------------------
# Vault passthrough (public surface for key management)
# ---------------------------------------------------------------------------


@router.post("/vault/keys", tags=["vault"])
async def store_vault_key(body: dict[str, Any]) -> dict[str, Any]:
    """Store a new secret in the vault (name + encrypted value)."""
    try:
        return await _call_internal(
            "POST",
            f"{VAULT_URL}/vault/store",
            json=body,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Vault error: {exc}") from exc


@router.get("/vault/keys/{project_id}", tags=["vault"])
async def list_vault_keys(project_id: str) -> dict[str, Any]:
    """List key names for a project (values are never returned)."""
    try:
        return await _call_internal(
            "GET",
            f"{VAULT_URL}/vault/list/{project_id}",
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Vault error: {exc}") from exc
