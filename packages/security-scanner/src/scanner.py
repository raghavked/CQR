"""
CQR Security Scanner — KG graph path traversal orchestrator.

This module replaces the previous regex-based scanner with a true graph
path traversal approach. It:
  1. Fetches all nodes and CALLS/IMPORTS edges for a project from the KG engine.
  2. Runs the path traversal engine (traversal.py) to find unvalidated taint paths.
  3. Runs structural checks (orphaned imports, circular deps, hardcoded creds).
  4. Classifies all results into named PDR scan patterns (patterns.py).
  5. Stores findings in memory (to be replaced with Postgres in CP-3).

The scan is intentionally stateless — each call to scan_project() fetches a
fresh snapshot from the KG and returns the current findings.
"""
from __future__ import annotations

import logging
import os
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

# In-memory finding store — keyed by project_id
# TODO(CP-3): Replace with Postgres-backed persistent store
_findings: dict[str, list[dict[str, Any]]] = {}
_scan_history: dict[str, list[dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# KG data fetching
# ---------------------------------------------------------------------------


async def _fetch_project_graph(project_id: str) -> tuple[list[dict], list[dict]]:
    """
    Fetch all nodes and edges for a project from the KG engine.
    Returns (nodes, edges).
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Fetch all nodes
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

        # Fetch all edges
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
    Fetch a specific set of nodes (and their immediate neighbourhood edges)
    from the KG engine. Used for post-agent-edit partial scans.
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

        # Deduplicate by id
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
# Core scan logic
# ---------------------------------------------------------------------------


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
    Fetches all nodes and edges, runs all 4 scan passes, stores and returns findings.
    """
    nodes, edges = await _fetch_project_graph(project_id)
    findings = _run_scan(project_id, nodes, edges, task_id)

    # Replace stored findings for this project
    _findings[project_id] = findings

    # Append to scan history
    _scan_history.setdefault(project_id, []).append({
        "scanned_at": datetime.utcnow().isoformat(),
        "findings_count": len(findings),
        "task_id": task_id,
        "node_count": len(nodes),
        "edge_count": len(edges),
    })

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

    # Merge into stored findings (replace findings for the same node paths)
    existing = _findings.get(project_id, [])
    affected_ids = set(node_ids)
    # Keep findings that don't overlap with the re-scanned nodes
    kept = [f for f in existing if not any(n in affected_ids for n in f.get("node_path", []))]
    _findings[project_id] = kept + findings

    return findings


def get_findings(project_id: str) -> list[dict[str, Any]]:
    """Return the latest stored findings for a project."""
    return _findings.get(project_id, [])


def get_scan_history(project_id: str) -> list[dict[str, Any]]:
    """Return the scan history for a project (timestamps, counts, task IDs)."""
    return _scan_history.get(project_id, [])
