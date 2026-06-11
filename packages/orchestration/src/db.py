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


# ---------------------------------------------------------------------------
# Security findings and scan history CRUD (CP-4)
# ---------------------------------------------------------------------------


async def store_scan_results(
    scan_id: str,
    project_id: str,
    findings: list[dict[str, Any]],
    task_id: str | None = None,
    node_count: int = 0,
    edge_count: int = 0,
) -> None:
    """
    Persist a full scan result set to Postgres.
    Inserts one row into security_scan_history and one row per finding into
    security_findings. Both tables are keyed by scan_id for correlation.
    """
    import json as _json
    import uuid as _uuid

    critical_count = sum(1 for f in findings if f.get("severity", "").upper() == "CRITICAL")
    high_count = sum(1 for f in findings if f.get("severity", "").upper() == "HIGH")

    async with AsyncSessionLocal() as session:
        # Insert scan history row
        await session.execute(
            text(
                """
                INSERT INTO security_scan_history
                    (id, scan_id, project_id, task_id, findings_count,
                     critical_count, high_count, node_count, edge_count, scanned_at)
                VALUES
                    (:id, :scan_id, :project_id, :task_id, :findings_count,
                     :critical_count, :high_count, :node_count, :edge_count, :scanned_at)
                """
            ),
            {
                "id": str(_uuid.uuid4()),
                "scan_id": scan_id,
                "project_id": project_id,
                "task_id": task_id,
                "findings_count": len(findings),
                "critical_count": critical_count,
                "high_count": high_count,
                "node_count": node_count,
                "edge_count": edge_count,
                "scanned_at": datetime.utcnow(),
            },
        )

        # Insert individual findings
        for finding in findings:
            node_path = finding.get("node_path", [])
            await session.execute(
                text(
                    """
                    INSERT INTO security_findings
                        (id, project_id, task_id, scan_id, pattern, severity,
                         description, suggested_fix, node_path,
                         source_node_id, sink_node_id, created_at)
                    VALUES
                        (:id, :project_id, :task_id, :scan_id, :pattern, :severity,
                         :description, :suggested_fix, :node_path,
                         :source_node_id, :sink_node_id, :created_at)
                    """
                ),
                {
                    "id": str(_uuid.uuid4()),
                    "project_id": project_id,
                    "task_id": task_id,
                    "scan_id": scan_id,
                    "pattern": finding.get("pattern", "unknown"),
                    "severity": finding.get("severity", "LOW"),
                    "description": finding.get("description"),
                    "suggested_fix": finding.get("suggested_fix"),
                    "node_path": _json.dumps(node_path),
                    "source_node_id": node_path[0] if node_path else None,
                    "sink_node_id": node_path[-1] if len(node_path) > 1 else None,
                    "created_at": datetime.utcnow(),
                },
            )

        await session.commit()


async def get_latest_findings(project_id: str) -> list[dict[str, Any]]:
    """
    Return the findings from the most recent scan for a project.
    Each finding includes node_path as a Python list (deserialized from JSONB).
    """
    import json as _json

    async with AsyncSessionLocal() as session:
        # Find the most recent scan_id for this project
        result = await session.execute(
            text(
                """
                SELECT scan_id FROM security_scan_history
                WHERE project_id = :pid
                ORDER BY scanned_at DESC
                LIMIT 1
                """
            ),
            {"pid": project_id},
        )
        row = result.mappings().first()
        if not row:
            return []

        scan_id = row["scan_id"]

        # Fetch all findings for that scan
        result = await session.execute(
            text(
                """
                SELECT * FROM security_findings
                WHERE scan_id = :scan_id
                ORDER BY severity DESC, created_at ASC
                """
            ),
            {"scan_id": scan_id},
        )
        findings = []
        for r in result.mappings().all():
            f = dict(r)
            # Deserialize node_path from JSONB string
            if isinstance(f.get("node_path"), str):
                try:
                    f["node_path"] = _json.loads(f["node_path"])
                except Exception:  # noqa: BLE001
                    f["node_path"] = []
            # Ensure UUIDs are strings
            for k in ("id", "project_id", "task_id"):
                if f.get(k) is not None:
                    f[k] = str(f[k])
            findings.append(f)
        return findings


async def get_scan_history_db(project_id: str) -> list[dict[str, Any]]:
    """
    Return all scan history entries for a project, most recent first.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                """
                SELECT * FROM security_scan_history
                WHERE project_id = :pid
                ORDER BY scanned_at DESC
                """
            ),
            {"pid": project_id},
        )
        rows = []
        for r in result.mappings().all():
            row = dict(r)
            for k in ("id", "project_id", "task_id"):
                if row.get(k) is not None:
                    row[k] = str(row[k])
            rows.append(row)
        return rows
