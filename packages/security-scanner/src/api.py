"""
Security Scanner internal FastAPI router.

Implements all four endpoints from PDR Section 10.2:
  POST /security/scan           — Full project scan via KG path traversal
  POST /security/scan-nodes     — Targeted scan on specific node IDs (post-agent-edit)
  GET  /security/report/{id}    — Latest findings with paths, severities, suggested fixes
  GET  /security/history/{id}   — Scan history over time

All scan results are produced by graph path traversal — not regex matching.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .scanner import get_findings, get_scan_history, scan_nodes, scan_project

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CQR Security Scanner",
    description=(
        "KG graph path traversal vulnerability scanner (internal). "
        "Detects unvalidated taint flows, hardcoded credentials, orphaned imports, "
        "and circular dependencies via directed graph walks over the Knowledge Graph."
    ),
    version="0.2.0",
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    """Request body for POST /security/scan."""

    project_id: str
    task_id: str | None = None


class ScanNodesRequest(BaseModel):
    """
    Request body for POST /security/scan-nodes.
    Used after agent edits to re-scan only the affected nodes and their
    2-hop neighbourhood, avoiding a full project re-scan.
    """

    project_id: str
    node_ids: list[str]
    task_id: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Return service health status."""
    return {"status": "ok", "service": "security-scanner", "version": "0.2.0"}


@app.post("/security/scan", tags=["security"])
async def trigger_scan(body: ScanRequest) -> dict[str, Any]:
    """
    Run a full KG path traversal scan for a project.

    The scanner:
    1. Fetches all nodes and edges from the KG engine.
    2. Identifies source nodes (EnvRef, user-input functions).
    3. Walks CALLS edges up to 8 hops, flagging paths that reach sensitive
       sinks (sql_execute, shell_execute, file_write, log_output) without
       passing through a validation node.
    4. Runs structural checks: hardcoded credentials, orphaned imports,
       circular import cycles.
    5. Returns all findings with full node_path arrays.
    """
    findings = await scan_project(body.project_id, body.task_id)
    return {
        "project_id": body.project_id,
        "findings_count": len(findings),
        "findings": findings,
        "scanned_at": datetime.utcnow().isoformat(),
    }


@app.post("/security/scan-nodes", tags=["security"])
async def trigger_node_scan(body: ScanNodesRequest) -> dict[str, Any]:
    """
    Run a targeted scan on a specific set of KG node IDs and their 2-hop
    neighbourhood. Called automatically by the Orchestration layer after
    every agent edit to provide fast incremental security feedback.
    """
    if not body.node_ids:
        raise HTTPException(status_code=400, detail="node_ids must not be empty")

    findings = await scan_nodes(body.project_id, body.node_ids, body.task_id)
    return {
        "project_id": body.project_id,
        "scanned_node_ids": body.node_ids,
        "findings_count": len(findings),
        "findings": findings,
        "scanned_at": datetime.utcnow().isoformat(),
    }


@app.get("/security/report/{project_id}", tags=["security"])
async def get_report(project_id: str) -> dict[str, Any]:
    """
    Return the latest stored security findings for a project.
    Each finding includes the full node_path (ordered list of KG node IDs
    from source to sink), severity, description, and suggested fix.
    """
    findings = get_findings(project_id)
    return {
        "project_id": project_id,
        "findings": findings,
        "findings_count": len(findings),
        "retrieved_at": datetime.utcnow().isoformat(),
    }


@app.get("/security/history/{project_id}", tags=["security"])
async def get_history(project_id: str) -> dict[str, Any]:
    """
    Return the scan history for a project.
    Each entry contains the timestamp, finding count, task ID, and
    node/edge counts at the time of the scan. Used for trend views in the UI.
    """
    history = get_scan_history(project_id)
    return {
        "project_id": project_id,
        "history": history,
        "scan_count": len(history),
    }
