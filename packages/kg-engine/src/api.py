"""
KG Engine internal FastAPI router.
Consumed by Agent Bridge and Security Scanner — not exposed publicly.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .graph.crud import (
    get_call_chain,
    get_env_refs,
    get_node,
    get_subgraph,
    record_agent_edit,
    search_nodes,
)
from .graph.schema import get_connection
from .ingestion.pipeline import ingest_project

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CQR KG Engine",
    description="Knowledge Graph ingestion and query service (internal).",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    """Request body for POST /kg/ingest."""

    project_id: str
    repo_path: str | None = None  # None = re-ingest using stored path


class MarkAgentEditRequest(BaseModel):
    """Request body for POST /kg/mark-agent-edit."""

    project_id: str
    node_id: str
    task_id: str
    agent: str = "claude"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Return service health status."""
    return {"status": "ok", "service": "kg-engine"}


@app.post("/kg/ingest", tags=["kg"])
async def ingest(body: IngestRequest) -> dict[str, Any]:
    """Ingest or re-ingest a project directory into the Knowledge Graph."""
    repo_path = body.repo_path or os.getenv("DEFAULT_REPO_PATH", "/workspace")
    summary = ingest_project(body.project_id, repo_path)
    return {"status": "ok", "summary": summary}


@app.get("/kg/subgraph", tags=["kg"])
async def subgraph(
    project_id: str = Query(...),
    node_id: str = Query(...),
    hops: int = Query(default=2, ge=1, le=5),
) -> dict[str, Any]:
    """
    Return nodes and edges within N hops of a node.
    Response includes token_estimate for Agent Bridge budget planning.
    """
    conn = get_connection(project_id)
    result = get_subgraph(conn, project_id, node_id, hops)
    if not result["nodes"]:
        raise HTTPException(status_code=404, detail="Node not found or no neighbours")
    return result


@app.get("/kg/node/{node_id}", tags=["kg"])
async def get_single_node(
    node_id: str,
    project_id: str = Query(...),
) -> dict[str, Any]:
    """Return a single KG node with all its properties."""
    conn = get_connection(project_id)
    node = get_node(conn, project_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


@app.get("/kg/search", tags=["kg"])
async def search(
    project_id: str = Query(...),
    q: str = Query(..., description="Search query string"),
) -> list[dict[str, Any]]:
    """Full-text search across node names and docstrings."""
    conn = get_connection(project_id)
    return search_nodes(conn, project_id, q)


@app.get("/kg/env-refs/{key}", tags=["kg"])
async def env_refs(
    key: str,
    project_id: str = Query(...),
) -> list[dict[str, Any]]:
    """Return all KG nodes referencing a specific .env key name."""
    conn = get_connection(project_id)
    return get_env_refs(conn, project_id, key)


@app.get("/kg/call-chain/{fn_id}", tags=["kg"])
async def call_chain(
    fn_id: str,
    project_id: str = Query(...),
) -> dict[str, Any]:
    """Return the full call chain upstream and downstream of a function node."""
    conn = get_connection(project_id)
    return get_call_chain(conn, project_id, fn_id)


@app.post("/kg/mark-agent-edit", tags=["kg"])
async def mark_agent_edit(body: MarkAgentEditRequest) -> dict[str, str]:
    """Record that an agent modified a KG node (audit trail)."""
    conn = get_connection(body.project_id)
    record_agent_edit(conn, body.project_id, body.node_id, body.task_id, body.agent)
    return {"status": "ok"}
