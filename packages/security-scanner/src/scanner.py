"""
CQR Security Scanner — KG graph path traversal orchestrator.

CP-4 changes:
  - scan_project() and scan_nodes() now POST results to the Orchestration
    persistence endpoint (POST /internal/security/store) instead of storing
    in in-memory dicts.
  - get_findings() and get_scan_history() now fetch from Orchestration
    (GET /internal/security/findings/{project_id} and
     GET /internal/security/history/{project_id}).
  - The scanner itself remains stateless — all state lives in Postgres.

The scan is intentionally stateless — each call to scan_project() fetches a
fresh snapshot from the KG and returns the current findings.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any

import httpx

from .patterns import (
    check_circular_dependencies,
    check_hardcoded_credentials,
    check_orphaned_imports,
    classify_traversal_result,
)
from .traversal import scan_graph

logger = logging.getLogger(__name__)

KG_ENGINE_URL = os.getenv("KG_ENGINE_URL", "http://localhost:8001")
ORCHESTRATION_URL = os.getenv("ORCHESTRATION_URL", "http://localhost:8000")


# ---------------------------------------------------------------------------
# KG data fetching
# ---------------------------------------------------------------------------


async def _fetch_project_graph(project_id: str) -> tuple[list[dict], list[dict]]:
    """
    Fetch all nodes and edges for a project from the KG engine.
    Returns (nodes, edges).
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            nodes_resp = await client.get(
                f"{KG_ENGINE_URL}/kg/nodes",
                params={"project_id": project_id},
            )
            nodes_resp.raise_for_status()
            nodes = nodes_resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("KG nodes fetch failed for project %s: %s", project_id, exc)
            nodes = []

        try:
            edges_resp = await client.get(
                f"{KG_ENGINE_URL}/kg/edges",
                params={"project_id": project_id},
            )
            edges_resp.raise_for_status()
            edges = edges_resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("KG edges fetch failed for project %s: %s", project_id, exc)
            edges = []

    return nodes, edges


async def _fetch_nodes_by_ids(
    project_id: str,
    node_ids: list[str],
) -> tuple[list[dict], list[dict]]:
    """
    Fetch a specific set of nodes (and their 2-hop neighbourhood) from the KG engine.
    Used for post-agent-edit partial scans.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        nodes: list[dict] = []
        edges: list[dict] = []

        for node_id in node_ids:
            try:
                resp = await client.get(
                    f"{KG_ENGINE_URL}/kg/subgraph",
                    params={"project_id": project_id, "node_id": node_id, "hops": 2},
                )
                resp.raise_for_status()
                subgraph = resp.json()
                nodes.extend(subgraph.get("nodes", []))
                edges.extend(subgraph.get("edges", []))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Subgraph fetch failed for node %s: %s", node_id, exc)

        # Deduplicate
        seen_nodes: set[str] = set()
        unique_nodes = []
        for n in nodes:
            if n["id"] not in seen_nodes:
                seen_nodes.add(n["id"])
                unique_nodes.append(n)

        seen_edges: set[tuple] = set()
        unique_edges = []
        for e in edges:
            key = (e.get("from_id"), e.get("to_id"), e.get("edge_type"))
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(e)

    return unique_nodes, unique_edges


# ---------------------------------------------------------------------------
# Postgres persistence (via Orchestration internal endpoint)
# ---------------------------------------------------------------------------


async def _store_scan_results(
    scan_id: str,
    project_id: str,
    findings: list[dict[str, Any]],
    task_id: str | None,
    node_count: int,
    edge_count: int,
) -> None:
    """
    POST scan results to the Orchestration internal persistence endpoint.
    Falls back to a warning log if Orchestration is unreachable.
    """
    payload = {
        "scan_id": scan_id,
        "project_id": project_id,
        "task_id": task_id,
        "findings": findings,
        "node_count": node_count,
        "edge_count": edge_count,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{ORCHESTRATION_URL}/internal/security/store",
                json=payload,
            )
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            '{"event": "scan_persist_failed", "project_id": "%s", "error": "%s"}',
            project_id,
            str(exc),
        )


async def _fetch_findings_from_db(project_id: str) -> list[dict[str, Any]]:
    """Fetch latest findings from Orchestration Postgres store."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{ORCHESTRATION_URL}/internal/security/findings/{project_id}",
            )
            resp.raise_for_status()
            return resp.json().get("findings", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch findings from DB: %s", exc)
        return []


async def _fetch_history_from_db(project_id: str) -> list[dict[str, Any]]:
    """Fetch scan history from Orchestration Postgres store."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{ORCHESTRATION_URL}/internal/security/history/{project_id}",
            )
            resp.raise_for_status()
            return resp.json().get("history", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch scan history from DB: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------


def _normalise_nodes(nodes: list[dict]) -> list[dict]:
    """
    Normalise KG node property keys returned by Kuzu.
    Kuzu prefixes all property names with 'n.' (e.g. 'n.name', 'n.signature').
    The traversal engine expects un-prefixed keys ('name', 'signature').
    """
    normalised = []
    for node in nodes:
        n2 = dict(node)
        raw_props = node.get("properties", {})
        n2["properties"] = {
            k[2:] if k.startswith("n.") else k: v
            for k, v in raw_props.items()
        }
        normalised.append(n2)
    return normalised


def _run_scan(
    project_id: str,
    nodes: list[dict],
    edges: list[dict],
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Run all scan passes over the provided nodes and edges.
    Returns a list of SecurityFinding dicts.
    """
    findings: list[dict[str, Any]] = []

    # Normalise node property keys (strip Kuzu's 'n.' prefix)
    nodes = _normalise_nodes(nodes)

    # --- Pass 1: Path traversal (taint flow analysis) ---
    traversal_results = scan_graph(nodes, edges)
    for result in traversal_results:
        finding = classify_traversal_result(result, project_id)
        if finding:
            if task_id:
                finding["task_id"] = task_id
            findings.append(finding)
            logger.warning(
                '{"event": "security_finding", "project_id": "%s", "pattern": "%s", '
                '"severity": "%s", "path_length": %d}',
                project_id,
                finding["pattern"],
                finding["severity"],
                len(finding["node_path"]),
            )

    # --- Pass 2: Hardcoded credentials (structural) ---
    cred_findings = check_hardcoded_credentials(nodes, project_id)
    for f in cred_findings:
        if task_id:
            f["task_id"] = task_id
    findings.extend(cred_findings)

    # --- Pass 3: Orphaned imports (structural) ---
    orphan_findings = check_orphaned_imports(nodes, edges, project_id)
    for f in orphan_findings:
        if task_id:
            f["task_id"] = task_id
    findings.extend(orphan_findings)

    # --- Pass 4: Circular dependencies (structural) ---
    circular_findings = check_circular_dependencies(nodes, edges, project_id)
    for f in circular_findings:
        if task_id:
            f["task_id"] = task_id
    findings.extend(circular_findings)

    return findings


# ---------------------------------------------------------------------------
# Public scan API
# ---------------------------------------------------------------------------


async def scan_project(
    project_id: str,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Run a full scan of a project's KG.
    Fetches all nodes and edges, runs all 4 scan passes, persists to Postgres.
    """
    nodes, edges = await _fetch_project_graph(project_id)
    findings = _run_scan(project_id, nodes, edges, task_id)

    scan_id = str(uuid.uuid4())
    await _store_scan_results(
        scan_id=scan_id,
        project_id=project_id,
        findings=findings,
        task_id=task_id,
        node_count=len(nodes),
        edge_count=len(edges),
    )

    return findings


async def scan_nodes(
    project_id: str,
    node_ids: list[str],
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Run a targeted scan on a specific set of node IDs and their 2-hop neighbourhood.
    Used after agent edits to avoid re-scanning the entire project.
    """
    nodes, edges = await _fetch_nodes_by_ids(project_id, node_ids)
    findings = _run_scan(project_id, nodes, edges, task_id)

    scan_id = str(uuid.uuid4())
    await _store_scan_results(
        scan_id=scan_id,
        project_id=project_id,
        findings=findings,
        task_id=task_id,
        node_count=len(nodes),
        edge_count=len(edges),
    )

    return findings


async def get_findings(project_id: str) -> list[dict[str, Any]]:
    """Return the latest stored findings for a project from Postgres."""
    return await _fetch_findings_from_db(project_id)


async def get_scan_history(project_id: str) -> list[dict[str, Any]]:
    """Return the scan history for a project from Postgres."""
    return await _fetch_history_from_db(project_id)
