"""
Orchestration internal security endpoints (CP-4).

These endpoints are called by the Security Scanner service to persist scan
results to Postgres and retrieve them. They are NOT part of the public API.
They are mounted at /internal/security/* and should be firewall-restricted
to internal service traffic only (enforced via X-CQR-IPC-Token header in
production; token check omitted here for dev simplicity).

Endpoints:
  POST /internal/security/store                    — persist scan results
  GET  /internal/security/findings/{project_id}   — latest findings
  GET  /internal/security/history/{project_id}    — scan history
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from .db import get_latest_findings, get_scan_history_db, store_scan_results

logger = logging.getLogger(__name__)

internal_security_router = APIRouter(prefix="/internal/security", tags=["internal-security"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class StoreScanRequest(BaseModel):
    """Payload sent by the Security Scanner to persist a scan result."""
    scan_id: str
    project_id: str
    task_id: str | None = None
    findings: list[dict[str, Any]] = []
    node_count: int = 0
    edge_count: int = 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@internal_security_router.post("/store")
async def store_scan(body: StoreScanRequest) -> dict[str, Any]:
    """
    Persist a full scan result set to Postgres.
    Called by the Security Scanner after every scan_project() or scan_nodes() run.
    """
    await store_scan_results(
        scan_id=body.scan_id,
        project_id=body.project_id,
        findings=body.findings,
        task_id=body.task_id,
        node_count=body.node_count,
        edge_count=body.edge_count,
    )
    logger.info(
        '{"event": "scan_stored", "project_id": "%s", "scan_id": "%s", "findings": %d}',
        body.project_id,
        body.scan_id,
        len(body.findings),
    )
    return {
        "stored": True,
        "scan_id": body.scan_id,
        "findings_count": len(body.findings),
    }


@internal_security_router.get("/findings/{project_id}")
async def get_findings_endpoint(project_id: str) -> dict[str, Any]:
    """Return the latest findings for a project from Postgres."""
    findings = await get_latest_findings(project_id)
    return {
        "project_id": project_id,
        "findings": findings,
        "findings_count": len(findings),
    }


@internal_security_router.get("/history/{project_id}")
async def get_history_endpoint(project_id: str) -> dict[str, Any]:
    """Return the full scan history for a project from Postgres."""
    history = await get_scan_history_db(project_id)
    return {
        "project_id": project_id,
        "history": history,
        "scan_count": len(history),
    }
