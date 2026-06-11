"""
KG Graph Path Traversal Engine for the CQR Security Scanner.

Architecture
------------
The scanner works by performing directed graph walks over the Knowledge Graph,
following CALLS edges from *source* nodes toward *sink* nodes. A path is flagged
as a vulnerability when it reaches a sink without passing through a *validation*
node along the way.

Terminology
-----------
Source node  — A node that introduces untrusted or sensitive data into the call
               graph. Two categories:
               1. EnvRef nodes  — functions that read environment variables
               2. User-input functions — functions whose name/signature suggests
                  they receive external input (e.g., request handlers, parse_*,
                  read_*, get_*, from_request)

Sink node    — A function that performs a sensitive operation:
               - SQL execution  (cursor.execute, session.query, raw SQL strings)
               - Shell execution (subprocess, os.system, exec, eval)
               - File write     (open(..., 'w'), write_file, shutil.copy)
               - Logging        (logger.*, logging.*, print with format strings)

Validation node — A function that sanitises or validates data before it reaches
                  a sink. If ANY node on the path from source to sink is a
                  validation node, the path is considered safe and not reported.
                  Examples: validate_*, sanitize_*, escape_*, parameterize_*,
                  quote_*, check_*, verify_*, assert_*

Algorithm
---------
For each source node S in the project KG:
  1. BFS/DFS forward along CALLS edges up to MAX_DEPTH hops.
  2. For every node N visited:
     a. If N is a validation node → mark this path branch as validated; prune.
     b. If N is a sink node       → record the full path S→…→N as a finding.
  3. Deduplicate findings by (source_id, sink_id, pattern).

The node_path field in SecurityFinding contains the ordered list of KG node IDs
from source to sink, giving the exact vulnerability chain for the UI to render.
"""
from __future__ import annotations

import logging
import re
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sink classification
# ---------------------------------------------------------------------------

# Function names / signatures that indicate a SQL execution sink
_SQL_SINK_NAMES = re.compile(
    r"\b(execute|executemany|executescript|raw|query|cursor\.execute|"
    r"session\.execute|engine\.execute|text\(|connection\.execute)\b",
    re.IGNORECASE,
)

# Function names / signatures that indicate a shell execution sink
_SHELL_SINK_NAMES = re.compile(
    r"\b(subprocess|os\.system|os\.popen|popen|exec|execv|execve|"
    r"check_output|check_call|call|run|Popen|eval|compile)\b",
    re.IGNORECASE,
)

# Function names / signatures that indicate a file-write sink
_FILE_WRITE_SINK_NAMES = re.compile(
    r"\b(open|write|writelines|write_file|shutil\.copy|shutil\.move|"
    r"copyfile|copyfileobj|dump|save)\b",
    re.IGNORECASE,
)

# Function names / signatures that indicate a logging sink
_LOG_SINK_NAMES = re.compile(
    r"\b(logging\.|logger\.|log\.|print|debug|info|warning|error|critical|"
    r"exception|audit)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Validation node classification
# ---------------------------------------------------------------------------

_VALIDATION_NAMES = re.compile(
    r"(?i)\b(validate|sanitize|sanitise|escape|parameteriz|quote|check|verify|"
    r"assert|clean|filter|strip|encode|decode|hash|hmac|sign|authenticate|"
    r"authorize|permit|allow|guard|protect|safe)(?:\b|_)",
)

# ---------------------------------------------------------------------------
# Source node classification
# ---------------------------------------------------------------------------

_USER_INPUT_NAMES = re.compile(
    r"\b(parse|read|get|from_request|handle|receive|accept|load|fetch|"
    r"deserializ|decode|input|request|form|body|param|arg|argv)\b",
    re.IGNORECASE,
)

# Maximum hop depth for path traversal
MAX_DEPTH = 8


# ---------------------------------------------------------------------------
# Node classification helpers
# ---------------------------------------------------------------------------


def _node_text(node: dict[str, Any]) -> str:
    """Return the combined text of a node's name, signature, and docstring."""
    props = node.get("properties", {})
    return " ".join(filter(None, [
        props.get("name", ""),
        props.get("signature", ""),
        props.get("docstring", ""),
    ]))


def is_sink(node: dict[str, Any]) -> tuple[bool, str]:
    """
    Determine whether a node is a sensitive sink.
    Returns (is_sink, sink_type).
    """
    text = _node_text(node)
    if _SQL_SINK_NAMES.search(text):
        return True, "sql_execute"
    if _SHELL_SINK_NAMES.search(text):
        return True, "shell_execute"
    if _FILE_WRITE_SINK_NAMES.search(text):
        return True, "file_write"
    if _LOG_SINK_NAMES.search(text):
        return True, "log_output"
    return False, ""


def is_validation(node: dict[str, Any]) -> bool:
    """Return True if a node performs validation or sanitisation."""
    return bool(_VALIDATION_NAMES.search(_node_text(node)))


def is_source(node: dict[str, Any]) -> bool:
    """
    Return True if a node is a taint source.
    Sources are: EnvRef nodes, or Function nodes with user-input-style names.
    """
    if node.get("type") == "EnvRef":
        return True
    if node.get("type") == "Function":
        return bool(_USER_INPUT_NAMES.search(_node_text(node)))
    return False


# ---------------------------------------------------------------------------
# Graph traversal
# ---------------------------------------------------------------------------


def traverse_paths(
    source_id: str,
    adjacency: dict[str, list[str]],
    node_map: dict[str, dict[str, Any]],
    max_depth: int = MAX_DEPTH,
) -> list[dict[str, Any]]:
    """
    BFS from source_id along CALLS edges.
    Returns a list of vulnerability path dicts:
      {
        "path": [node_id, ...],          # ordered source → sink
        "sink_type": str,                # sql_execute | shell_execute | ...
        "validated": bool,               # True if a validation node was on path
      }

    Only paths that reach a sink WITHOUT a validation node are returned.
    """
    findings: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, str]] = set()  # (source_id, sink_id) dedup

    # BFS state: (current_node_id, path_so_far, validated_on_path)
    queue: deque[tuple[str, list[str], bool]] = deque()
    queue.append((source_id, [source_id], False))

    while queue:
        current_id, path, validated = queue.popleft()

        if len(path) > max_depth:
            continue

        current_node = node_map.get(current_id)
        if not current_node:
            continue

        # Check if this node is a validation gate
        if is_validation(current_node) and current_id != source_id:
            validated = True

        # Check if this node is a sink
        sink_flag, sink_type = is_sink(current_node)
        if sink_flag and current_id != source_id:
            dedup_key = (source_id, current_id)
            if dedup_key not in seen_paths:
                seen_paths.add(dedup_key)
                findings.append({
                    "path": list(path),
                    "sink_type": sink_type,
                    "validated": validated,
                })
            # Don't traverse further past a sink
            continue

        # Expand neighbours via CALLS edges
        for neighbour_id in adjacency.get(current_id, []):
            if neighbour_id not in path:  # Prevent cycles
                queue.append((neighbour_id, path + [neighbour_id], validated))

    return findings


# ---------------------------------------------------------------------------
# Full project scan
# ---------------------------------------------------------------------------


def scan_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Run the full path traversal scan over a project's KG nodes and edges.

    Parameters
    ----------
    nodes : list of KG node dicts (id, type, properties)
    edges : list of KG edge dicts (from_id, edge_type, to_id)

    Returns
    -------
    List of raw traversal results, each containing:
      - source_id, source_type, path, sink_type, validated
    Only unvalidated paths are included.
    """
    # Build adjacency map: node_id → [neighbour_ids via CALLS]
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("edge_type") == "CALLS":
            src = edge.get("from_id")
            dst = edge.get("to_id")
            if src and dst:
                adjacency.setdefault(src, []).append(dst)

    # Build node lookup map
    node_map: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}

    results: list[dict[str, Any]] = []

    for node in nodes:
        if not is_source(node):
            continue

        source_id = node["id"]
        paths = traverse_paths(source_id, adjacency, node_map)

        for p in paths:
            if p["validated"]:
                # Path passes through a validation node — safe
                continue
            results.append({
                "source_id": source_id,
                "source_type": node.get("type", "Unknown"),
                "path": p["path"],
                "sink_type": p["sink_type"],
                "validated": False,
            })

    logger.info(
        '{"event": "traversal_complete", "sources_scanned": %d, "unvalidated_paths": %d}',
        sum(1 for n in nodes if is_source(n)),
        len(results),
    )
    return results
