"""
Async Postgres database layer for the Orchestration service.

Replaces the in-memory _projects and _tasks dicts with persistent storage.
Uses SQLAlchemy 2.x async engine + asyncpg driver.

Tables are defined in alembic/versions/0001_initial.py and must be created
before the service starts (run: alembic upgrade head).
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Engine setup
# ---------------------------------------------------------------------------

_DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://cqr:cqr@postgres:5432/cqr")

# SQLAlchemy requires the asyncpg driver scheme
_ASYNC_URL = _DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(_ASYNC_URL, pool_size=5, max_overflow=10, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


async def create_project(
    project_id: str,
    name: str,
    repo_path: str,
    status: str = "provisioning",
) -> dict[str, Any]:
    """Insert a new project row and return it as a dict."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                """
                INSERT INTO projects (id, name, repo_path, status, created_at)
                VALUES (:id, :name, :repo_path, :status, :created_at)
                """
            ),
            {
                "id": project_id,
                "name": name,
                "repo_path": repo_path,
                "status": status,
                "created_at": datetime.utcnow(),
            },
        )
        await session.commit()
    return await get_project(project_id)  # type: ignore[return-value]


async def get_project(project_id: str) -> dict[str, Any] | None:
    """Return a project row as a dict, or None if not found."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT * FROM projects WHERE id = :id"),
            {"id": project_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


async def list_projects() -> list[dict[str, Any]]:
    """Return all project rows."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT * FROM projects ORDER BY created_at DESC"))
        return [dict(r) for r in result.mappings().all()]


async def update_project(project_id: str, **fields: Any) -> None:
    """Update arbitrary columns on a project row."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(f"UPDATE projects SET {set_clause} WHERE id = :id"),
            {"id": project_id, **fields},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------


async def create_task(
    task_id: str,
    project_id: str,
    description: str,
    agent: str,
    budget_tier: str,
    status: str = "queued",
) -> dict[str, Any]:
    """Insert a new task row and return it as a dict."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                """
                INSERT INTO tasks
                    (id, project_id, description, agent, budget_tier, status, created_at)
                VALUES
                    (:id, :project_id, :description, :agent, :budget_tier, :status, :created_at)
                """
            ),
            {
                "id": task_id,
                "project_id": project_id,
                "description": description,
                "agent": agent,
                "budget_tier": budget_tier,
                "status": status,
                "created_at": datetime.utcnow(),
            },
        )
        await session.commit()
    return await get_task(task_id)  # type: ignore[return-value]


async def get_task(task_id: str) -> dict[str, Any] | None:
    """Return a task row as a dict, or None if not found."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT * FROM tasks WHERE id = :id"),
            {"id": task_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


async def list_tasks(project_id: str | None = None) -> list[dict[str, Any]]:
    """Return all tasks, optionally filtered by project_id."""
    async with AsyncSessionLocal() as session:
        if project_id:
            result = await session.execute(
                text("SELECT * FROM tasks WHERE project_id = :pid ORDER BY created_at DESC"),
                {"pid": project_id},
            )
        else:
            result = await session.execute(
                text("SELECT * FROM tasks ORDER BY created_at DESC")
            )
        return [dict(r) for r in result.mappings().all()]


async def update_task(task_id: str, **fields: Any) -> None:
    """Update arbitrary columns on a task row."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(f"UPDATE tasks SET {set_clause} WHERE id = :id"),
            {"id": task_id, **fields},
        )
        await session.commit()
