"""
Pattern classifier for the CQR Security Scanner.

Maps raw graph traversal results onto the 7 named scan patterns defined in
PDR Section 10.1, assigning severity, description, and suggested fix for each.

PDR Scan Patterns
-----------------
1. Unvalidated EnvRef    — EnvRef node reaches any sink without validation  (Medium)
2. Secret in Log         — EnvRef node reaches a log_output sink            (High)
3. SQL Injection Path    — Any source reaches sql_execute without validation (Critical)
4. Unescaped Shell Exec  — Any source reaches shell_execute without validation (Critical)
5. Hardcoded Credential  — Detected via KG node property inspection         (High)
6. Orphaned Import       — Import node with no incoming CALLS edges         (Low)
7. Circular Dependency   — Import cycle in the KG graph                     (Medium)

Patterns 1–4 are produced by the path traversal engine (traversal.py).
Patterns 5–7 are produced by structural graph inspection (no path traversal needed).
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded credential detection (structural, not path-based)
# ---------------------------------------------------------------------------

_CREDENTIAL_PATTERNS = [
    re.compile(r'(?i)(password|passwd|secret|api_key|apikey|token|auth)\s*=\s*["\'][^"\']{6,}["\']'),
    re.compile(r'(?i)(?:sk-|ghp_|xoxb-|AKIA|AIza)[A-Za-z0-9/_\-]{10,}'),
    re.compile(r'(?i)-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----'),
]


def _has_hardcoded_credential(node: dict[str, Any]) -> bool:
    """Return True if a node's properties contain a hardcoded credential pattern."""
    props = node.get("properties", {})
    text = " ".join(str(v) for v in props.values())
    return any(p.search(text) for p in _CREDENTIAL_PATTERNS)


# ---------------------------------------------------------------------------
# Pattern → finding factory
# ---------------------------------------------------------------------------

_PATTERN_META: dict[str, dict[str, str]] = {
    "unvalidated_env_ref": {
        "severity": "medium",
        "description": (
            "An environment variable is read and flows into a sensitive operation "
            "without any validation or type-checking on the path."
        ),
        "suggested_fix": (
            "Add a validation node between the EnvRef read and the sink. "
            "Use os.environ.get('KEY', default) with explicit type casting and "
            "a fallback, or route through a dedicated config-validation function."
        ),
    },
    "secret_in_log": {
        "severity": "high",
        "description": (
            "An environment variable (likely a secret) flows into a logging call. "
            "This may expose credentials in log files or observability pipelines."
        ),
        "suggested_fix": (
            "Never log raw environment variable values. Redact secrets before "
            "logging: use a masking helper such as mask_secret(value) that "
            "replaces all but the first/last characters with asterisks."
        ),
    },
    "sql_injection_path": {
        "severity": "critical",
        "description": (
            "A taint source (EnvRef or user-input function) flows into a SQL "
            "execution call without passing through a sanitisation or "
            "parameterisation node. This is a potential SQL injection vector."
        ),
        "suggested_fix": (
            "Use parameterised queries exclusively. Replace string concatenation "
            "with bound parameters: cursor.execute('SELECT ... WHERE id = %s', (user_id,)). "
            "Never interpolate untrusted data directly into SQL strings."
        ),
    },
    "unescaped_shell_exec": {
        "severity": "critical",
        "description": (
            "A taint source flows into a shell execution call (subprocess, os.system, "
            "eval, exec) without sanitisation. This is a potential command injection vector."
        ),
        "suggested_fix": (
            "Pass arguments as a list to subprocess (not shell=True). "
            "Validate and whitelist all inputs before passing to shell commands. "
            "Prefer higher-level APIs over raw shell execution where possible."
        ),
    },
    "hardcoded_credential": {
        "severity": "high",
        "description": (
            "A string literal matching a known credential pattern (API key, password, "
            "private key) was found in a KG node's properties. Hardcoded secrets are "
            "exposed in source control and agent context."
        ),
        "suggested_fix": (
            "Move all credentials to the CQR Vault. Reference them via "
            "os.environ.get('KEY_NAME') and store the real value using the vault API. "
            "Rotate any exposed credentials immediately."
        ),
    },
    "orphaned_import": {
        "severity": "low",
        "description": (
            "An Import node has no incoming CALLS edges — it is imported but never "
            "used. Dead imports increase attack surface and dependency risk without "
            "providing functionality."
        ),
        "suggested_fix": (
            "Remove the unused import. Run a linter (e.g., autoflake, pylint) to "
            "identify and clean up all dead imports in the project."
        ),
    },
    "circular_dependency": {
        "severity": "medium",
        "description": (
            "A circular import chain was detected in the KG graph. Circular "
            "dependencies cause unpredictable module initialisation order and "
            "can mask import-time side effects."
        ),
        "suggested_fix": (
            "Refactor to break the cycle. Common strategies: extract shared "
            "code into a third module, use lazy imports inside functions, or "
            "apply dependency inversion (depend on abstractions, not concretions)."
        ),
    },
}


def make_finding(
    project_id: str,
    pattern_name: str,
    node_path: list[str],
    extra_description: str = "",
) -> dict[str, Any]:
    """
    Create a SecurityFinding dict for a given pattern and node path.
    """
    meta = _PATTERN_META.get(pattern_name, {})
    description = meta.get("description", "")
    if extra_description:
        description = f"{description} {extra_description}".strip()

    return {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "pattern": pattern_name,
        "severity": meta.get("severity", "medium"),
        "node_path": node_path,
        "description": description,
        "suggested_fix": meta.get("suggested_fix"),
        "detected_at": datetime.utcnow().isoformat(),
        "resolved": False,
    }


# ---------------------------------------------------------------------------
# Classify traversal results into named patterns
# ---------------------------------------------------------------------------


def classify_traversal_result(
    result: dict[str, Any],
    project_id: str,
) -> dict[str, Any] | None:
    """
    Map a single path traversal result to a named PDR scan pattern.
    Returns a SecurityFinding dict, or None if the result does not match
    any named pattern (should not happen in practice).
    """
    source_type = result.get("source_type", "")
    sink_type = result.get("sink_type", "")
    path = result.get("path", [])

    # Pattern: Secret in Log — EnvRef → log_output (most specific, check first)
    if source_type == "EnvRef" and sink_type == "log_output":
        return make_finding(project_id, "secret_in_log", path)

    # Pattern: SQL Injection Path — any source → sql_execute (sink-driven, critical)
    if sink_type == "sql_execute":
        return make_finding(project_id, "sql_injection_path", path)

    # Pattern: Unescaped Shell Exec — any source → shell_execute (sink-driven, critical)
    if sink_type == "shell_execute":
        return make_finding(project_id, "unescaped_shell_exec", path)

    # Pattern: Unvalidated EnvRef — EnvRef → any remaining sink (file_write, etc.)
    if source_type == "EnvRef":
        return make_finding(project_id, "unvalidated_env_ref", path)

    # Pattern: File write from tainted source (not a named PDR pattern but worth flagging)
    # TODO(AMBIGUITY): PDR does not name a file_write pattern explicitly — treating as
    # unescaped_shell_exec severity for now since it can lead to path traversal
    if sink_type == "file_write":
        return make_finding(
            project_id,
            "unescaped_shell_exec",
            path,
            extra_description="(Tainted data flows into a file-write operation.)",
        )

    return None


# ---------------------------------------------------------------------------
# Structural pattern checks (no traversal needed)
# ---------------------------------------------------------------------------


def check_hardcoded_credentials(
    nodes: list[dict[str, Any]],
    project_id: str,
) -> list[dict[str, Any]]:
    """
    Inspect all KG nodes for hardcoded credential patterns.
    Returns a list of SecurityFinding dicts.
    """
    findings = []
    for node in nodes:
        if _has_hardcoded_credential(node):
            findings.append(make_finding(project_id, "hardcoded_credential", [node["id"]]))
    return findings


def check_orphaned_imports(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    project_id: str,
) -> list[dict[str, Any]]:
    """
    Find Import nodes that have no incoming CALLS edges.
    An import that is never called is dead code with dependency risk.
    """
    # Build set of node IDs that are targets of any edge
    targeted_ids: set[str] = {e["to_id"] for e in edges if e.get("to_id")}

    findings = []
    for node in nodes:
        if node.get("type") == "Import" and node["id"] not in targeted_ids:
            findings.append(make_finding(project_id, "orphaned_import", [node["id"]]))
    return findings


def check_circular_dependencies(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    project_id: str,
) -> list[dict[str, Any]]:
    """
    Detect circular import chains using DFS cycle detection on IMPORTS edges.
    Returns one finding per cycle detected (deduplicated by cycle members).
    """
    # Build adjacency for IMPORTS edges only
    imports_adj: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("edge_type") == "IMPORTS":
            src = edge.get("from_id")
            dst = edge.get("to_id")
            if src and dst:
                imports_adj.setdefault(src, []).append(dst)

    visited: set[str] = set()
    rec_stack: set[str] = set()
    cycles: list[list[str]] = []

    def dfs(node_id: str, path: list[str]) -> None:
        visited.add(node_id)
        rec_stack.add(node_id)
        for neighbour in imports_adj.get(node_id, []):
            if neighbour not in visited:
                dfs(neighbour, path + [neighbour])
            elif neighbour in rec_stack:
                # Found a cycle — record the cycle path
                cycle_start = path.index(neighbour) if neighbour in path else 0
                cycles.append(path[cycle_start:] + [neighbour])
        rec_stack.discard(node_id)

    for node in nodes:
        if node.get("type") == "File" and node["id"] not in visited:
            dfs(node["id"], [node["id"]])

    # Deduplicate cycles by frozenset of members
    seen_cycles: set[frozenset] = set()
    findings = []
    for cycle in cycles:
        key = frozenset(cycle)
        if key not in seen_cycles:
            seen_cycles.add(key)
            findings.append(make_finding(project_id, "circular_dependency", cycle))

    return findings
