"""
Security Scanner internal FastAPI router.
Triggered after agent edits; results stored and served to Orchestration layer.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .scanner import get_findings, scan_project

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CQR Security Scanner",
    description="KG path traversal and vulnerability detection (internal).",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    """Request body for POST /security/scan."""

    project_id: str
    task_id: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Return service health status."""
    return {"status": "ok", "service": "security-scanner"}


@app.post("/security/scan", tags=["security"])
async def trigger_scan(body: ScanRequest) -> dict[str, Any]:
    """
    Trigger a security scan for a project.
    Runs KG path traversal against all registered vulnerability patterns.
    """
    findings = await scan_project(body.project_id, body.task_id)
    return {
        "project_id": body.project_id,
        "findings_count": len(findings),
        "findings": findings,
        "scanned_at": datetime.utcnow().isoformat(),
    }


@app.get("/security/report/{project_id}", tags=["security"])
async def get_report(project_id: str) -> dict[str, Any]:
    """Return all stored security findings for a project."""
    findings = get_findings(project_id)
    return {
        "project_id": project_id,
        "findings": findings,
        "scanned_at": datetime.utcnow().isoformat(),
    }
