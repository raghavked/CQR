"""
LSM Layer internal FastAPI router.
Consumed by Agent Bridge for proximity scoring and budget planning.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .embeddings.embedder import get_embedding
from .spatial.index import (
    budget_plan,
    delete_embedding,
    ensure_schema,
    proximity_search,
    upsert_embedding,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CQR LSM Layer",
    description="Latent Space Mapping — pgvector proximity scoring (internal).",
    version="0.1.0",
)


@app.on_event("startup")
async def startup() -> None:
    """Ensure pgvector schema is initialised on startup."""
    try:
        ensure_schema()
        logger.info("LSM schema initialised")
    except Exception as exc:  # noqa: BLE001
        logger.warning("LSM schema init failed (Postgres may not be ready): %s", exc)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class EmbedRequest(BaseModel):
    """Request body for POST /lsm/embed."""

    node_id: str
    project_id: str
    node_type: str
    snippet: str


class ProximityRequest(BaseModel):
    """Query parameters for GET /lsm/proximity."""

    project_id: str
    query: str | None = None
    node_id: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Return service health status."""
    return {"status": "ok", "service": "lsm-layer"}


@app.post("/lsm/embed", tags=["lsm"])
async def embed_node(body: EmbedRequest) -> dict[str, Any]:
    """Embed a node's code snippet and store the vector in pgvector."""
    embedding = get_embedding(body.snippet)
    upsert_embedding(
        node_id=body.node_id,
        project_id=body.project_id,
        node_type=body.node_type,
        snippet=body.snippet,
        embedding=embedding,
    )
    return {"status": "ok", "node_id": body.node_id, "dim": len(embedding)}


@app.get("/lsm/proximity", tags=["lsm"])
async def proximity(
    project_id: str = Query(...),
    query: str | None = Query(default=None),
    node_id: str | None = Query(default=None),
    threshold: float = Query(default=0.70, ge=0.0, le=1.0),
) -> list[dict[str, Any]]:
    """
    Return nodes ranked by proximity to a query string or node ID.
    At least one of `query` or `node_id` must be provided.
    """
    if not query and not node_id:
        raise HTTPException(status_code=400, detail="Provide either 'query' or 'node_id'")

    query_text = query or node_id
    embedding = get_embedding(query_text)  # type: ignore[arg-type]
    return proximity_search(project_id, embedding, threshold=threshold)


@app.get("/lsm/budget-plan", tags=["lsm"])
async def get_budget_plan(
    project_id: str = Query(...),
    task: str = Query(..., description="Task description to embed as semantic anchor"),
    budget_tier: str = Query(default="standard"),
) -> list[dict[str, Any]]:
    """
    Return the optimal node set for a task within the given token budget tier.
    """
    embedding = get_embedding(task)
    return budget_plan(project_id, embedding, budget_tier)


@app.delete("/lsm/node/{node_id}", tags=["lsm"])
async def remove_node_embedding(node_id: str) -> dict[str, str]:
    """Remove a node's embedding (called on KG node deletion)."""
    delete_embedding(node_id)
    return {"status": "ok", "node_id": node_id}
