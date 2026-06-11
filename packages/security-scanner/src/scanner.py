"""
CQR Security Scanner — KG path traversal and vulnerability detection.
Runs automatically after every agent edit and on manual trigger.
Results are emitted as security.alert WebSocket events and stored for reporting.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

KG_ENGINE_URL = os.getenv("KG_ENGINE_URL", "http://localhost:8001")

# ---------------------------------------------------------------------------
# Vulnerability scan patterns
# ---------------------------------------------------------------------------

# Each pattern: (name, severity, description, detection_fn)
# detection_fn(node: dict) -> bool

_HARDCODED_SECRET_PATTERNS = [
    re.compile(r'(?i)(password|passwd|secret|api_key|apikey|token|auth)\s*=\s*["\'][^"\']{6,}["\']'),
    re.compile(r'(?i)(?:sk-|ghp_|xoxb-|AKIA)[A-Za-z0-9]{10,}'),
]

_SQL_INJECTION_PATTERNS = [
    re.compile(r'(?i)(execute|query|cursor\.execute)\s*\(\s*["\'].*?\+'),
    re.compile(r'(?i)f["\'].*?SELECT.*?{.*?}'),
    re.compile(r'(?i)%\s*\(.*?\)\s*%\s*["\'].*?SELECT'),
]

_PATH_TRAVERSAL_PATTERNS = [
    re.compile(r'(?i)open\s*\(\s*.*?\+'),
    re.compile(r'(?i)os\.path\.join\s*\(.*?request'),
]

_COMMAND_INJECTION_PATTERNS = [
    re.compile(r'(?i)(os\.system|subprocess\.call|subprocess\.run)\s*\(.*?\+'),
    re.compile(r'(?i)eval\s*\(.*?input'),
]


def _check_hardcoded_secret(node: dict[str, Any]) -> bool:
    """Detect hardcoded credentials in a node's code snippet."""
    snippet = node.get("properties", {}).get("signature", "") + \
              node.get("properties", {}).get("docstring", "")
    for pattern in _HARDCODED_SECRET_PATTERNS:
        if pattern.search(snippet):
            return True
    return False


def _check_sql_injection(node: dict[str, Any]) -> bool:
    """Detect SQL injection patterns in a function node."""
    snippet = node.get("properties", {}).get("signature", "")
    for pattern in _SQL_INJECTION_PATTERNS:
        if pattern.search(snippet):
            return True
    return False


def _check_path_traversal(node: dict[str, Any]) -> bool:
    """Detect path traversal patterns in a function node."""
    snippet = node.get("properties", {}).get("signature", "")
    for pattern in _PATH_TRAVERSAL_PATTERNS:
        if pattern.search(snippet):
            return True
    return False


def _check_command_injection(node: dict[str, Any]) -> bool:
    """Detect command injection patterns in a function node."""
    snippet = node.get("properties", {}).get("signature", "")
    for pattern in _COMMAND_INJECTION_PATTERNS:
        if pattern.search(snippet):
            return True
    return False


# Pattern registry: (pattern_name, severity, description, check_fn)
SCAN_PATTERNS = [
    ("hardcoded_secret", "critical", "Hardcoded credential or API key detected", _check_hardcoded_secret),
    ("sql_injection", "high", "Potential SQL injection via string concatenation", _check_sql_injection),
    ("path_traversal", "high", "Potential path traversal via unsanitised input", _check_path_traversal),
    ("command_injection", "high", "Potential command injection via unsanitised input", _check_command_injection),
]

# In-memory finding store
# TODO(AMBIGUITY): Replace with Postgres-backed store in CP-3
_findings: dict[str, list[dict[str, Any]]] = {}


async def scan_project(project_id: str, task_id: str | None = None) -> list[dict[str, Any]]:
    """
    Scan all KG nodes for a project against all vulnerability patterns.
    Returns a list of SecurityFinding dicts.
    """
    new_findings: list[dict[str, Any]] = []

    # Fetch all function nodes from KG via search (empty query returns all)
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{KG_ENGINE_URL}/kg/search",
                params={"project_id": project_id, "q": ""},
            )
            response.raise_for_status()
            nodes = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("KG search failed during scan: %s", exc)
            nodes = []

        # For each node, fetch full details and run patterns
        for node_stub in nodes:
            node_id = node_stub.get("id")
            if not node_id:
                continue
            try:
                node_response = await client.get(
                    f"{KG_ENGINE_URL}/kg/node/{node_id}",
                    params={"project_id": project_id},
                )
                node_response.raise_for_status()
                node = node_response.json()
            except Exception:  # noqa: BLE001
                node = node_stub

            for pattern_name, severity, description, check_fn in SCAN_PATTERNS:
                if check_fn(node):
                    finding = {
                        "id": str(uuid.uuid4()),
                        "project_id": project_id,
                        "pattern": pattern_name,
                        "severity": severity,
                        "node_path": [node_id],
                        "description": description,
                        "suggested_fix": _suggest_fix(pattern_name),
                        "detected_at": datetime.utcnow().isoformat(),
                        "resolved": False,
                        "task_id": task_id,
                    }
                    new_findings.append(finding)
                    logger.warning(
                        '{"event": "security_finding", "project_id": "%s", "pattern": "%s", "severity": "%s", "node_id": "%s"}',
                        project_id,
                        pattern_name,
                        severity,
                        node_id,
                    )

    # Store findings
    _findings.setdefault(project_id, []).extend(new_findings)
    return new_findings


def get_findings(project_id: str) -> list[dict[str, Any]]:
    """Return all stored security findings for a project."""
    return _findings.get(project_id, [])


def _suggest_fix(pattern_name: str) -> str:
    """Return a suggested fix description for a known vulnerability pattern."""
    fixes = {
        "hardcoded_secret": "Move credentials to environment variables and reference via os.environ.get(). Never commit secrets to source control.",
        "sql_injection": "Use parameterised queries (e.g., cursor.execute('SELECT ... WHERE id = %s', (user_id,))) instead of string concatenation.",
        "path_traversal": "Validate and sanitise file paths. Use os.path.realpath() and verify the resolved path is within the expected directory.",
        "command_injection": "Avoid passing user input to shell commands. Use subprocess with a list of arguments (not shell=True) and validate all inputs.",
    }
    return fixes.get(pattern_name, "Review and remediate the flagged code pattern.")
