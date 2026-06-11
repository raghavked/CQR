"""
CRUD operations and subgraph query helpers for the Kuzu-backed Knowledge Graph.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

import kuzu

from .schema import get_connection

logger = logging.getLogger(__name__)

# Rough token estimate: 1 token ≈ 4 characters of serialised JSON
_CHARS_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# Node upsert helpers
# ---------------------------------------------------------------------------


def _node_id(node_type: str, project_id: str, *parts: str) -> str:
    """Generate a deterministic node ID from type, project, and key parts."""
    raw = f"{node_type}:{project_id}:{':'.join(parts)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def upsert_file_node(conn: kuzu.Connection, project_id: str, props: dict[str, Any]) -> str:
    """Insert or update a File node; returns the node ID."""
    node_id = _node_id("File", project_id, props["path"])
    # raw_token_count: use stored value if provided, else estimate from content length
    raw_token_count = props.get("raw_token_count")
    if raw_token_count is None:
        content = props.get("content", "")
        raw_token_count = len(content) // 4  # 1 token ≈ 4 chars
    conn.execute(
        """
        MERGE (f:File {id: $id})
        SET f.path = $path,
            f.language = $language,
            f.last_modified = $last_modified,
            f.hash = $hash,
            f.raw_token_count = $raw_token_count,
            f.project_id = $project_id
        """,
        {
            "id": node_id,
            "path": props["path"],
            "language": props.get("language", "unknown"),
            "last_modified": props.get("last_modified", 0.0),
            "hash": props.get("hash", ""),
            "raw_token_count": int(raw_token_count),
            "project_id": project_id,
        },
    )
    return node_id


def upsert_function_node(conn: kuzu.Connection, project_id: str, props: dict[str, Any]) -> str:
    """Insert or update a Function node; returns the node ID."""
    node_id = _node_id("Function", project_id, props["file_path"], props["name"], str(props.get("start_line", 0)))
    conn.execute(
        """
        MERGE (f:Function {id: $id})
        SET f.name = $name,
            f.file_path = $file_path,
            f.start_line = $start_line,
            f.end_line = $end_line,
            f.signature = $signature,
            f.docstring = $docstring,
            f.project_id = $project_id
        """,
        {
            "id": node_id,
            "name": props["name"],
            "file_path": props["file_path"],
            "start_line": props.get("start_line", 0),
            "end_line": props.get("end_line", 0),
            "signature": props.get("signature", ""),
            "docstring": props.get("docstring", ""),
            "project_id": project_id,
        },
    )
    return node_id


def upsert_class_node(conn: kuzu.Connection, project_id: str, props: dict[str, Any]) -> str:
    """Insert or update a Class node; returns the node ID."""
    node_id = _node_id("Class", project_id, props["file_path"], props["name"])
    conn.execute(
        """
        MERGE (c:Class {id: $id})
        SET c.name = $name,
            c.file_path = $file_path,
            c.start_line = $start_line,
            c.end_line = $end_line,
            c.base_classes = $base_classes,
            c.project_id = $project_id
        """,
        {
            "id": node_id,
            "name": props["name"],
            "file_path": props["file_path"],
            "start_line": props.get("start_line", 0),
            "end_line": props.get("end_line", 0),
            "base_classes": json.dumps(props.get("base_classes", [])),
            "project_id": project_id,
        },
    )
    return node_id


def upsert_import_node(conn: kuzu.Connection, project_id: str, props: dict[str, Any]) -> str:
    """Insert or update an Import node; returns the node ID."""
    node_id = _node_id("Import", project_id, props["source_file"], props["imported_module"])
    conn.execute(
        """
        MERGE (i:Import {id: $id})
        SET i.source_file = $source_file,
            i.imported_module = $imported_module,
            i.imported_names = $imported_names,
            i.project_id = $project_id
        """,
        {
            "id": node_id,
            "source_file": props["source_file"],
            "imported_module": props["imported_module"],
            "imported_names": json.dumps(props.get("imported_names", [])),
            "project_id": project_id,
        },
    )
    return node_id


def upsert_env_ref_node(conn: kuzu.Connection, project_id: str, props: dict[str, Any]) -> str:
    """Insert or update an EnvRef node; returns the node ID."""
    node_id = _node_id("EnvRef", project_id, props["file_path"], props["key_name"], str(props.get("line", 0)))
    conn.execute(
        """
        MERGE (e:EnvRef {id: $id})
        SET e.key_name = $key_name,
            e.file_path = $file_path,
            e.line = $line,
            e.project_id = $project_id
        """,
        {
            "id": node_id,
            "key_name": props["key_name"],
            "file_path": props["file_path"],
            "line": props.get("line", 0),
            "project_id": project_id,
        },
    )
    return node_id


# Edge existence cache: (from_id, to_id, edge_type) → True
# Prevents duplicate edge queries on the same connection within a single ingestion run.
_edge_cache: set[tuple[str, str, str]] = set()


def clear_edge_cache() -> None:
    """Clear the in-process edge deduplication cache. Call between ingestion runs."""
    _edge_cache.clear()


def add_edge(conn: kuzu.Connection, edge_type: str, from_id: str, to_id: str) -> None:
    """
    Create a directed edge between two nodes if it does not already exist.

    Idempotency strategy (Correction 4 / PDR §5.3):
      Kuzu does not support MERGE on relationship tables in all versions.
      We implement idempotency with a two-step check-then-create:
        1. Check the in-process cache (fast path — avoids DB round-trip for
           edges already created in this ingestion run).
        2. Query the DB to check for an existing edge (handles re-ingestion
           of a project that was previously ingested).
        3. Only CREATE if no existing edge is found.
    """
    cache_key = (from_id, to_id, edge_type)
    if cache_key in _edge_cache:
        return

    try:
        # Step 1: check whether the edge already exists in the DB
        check = conn.execute(
            f"""
            MATCH (a {{id: $from_id}})-[r:{edge_type}]->(b {{id: $to_id}})
            RETURN count(r) AS cnt
            """,
            {"from_id": from_id, "to_id": to_id},
        )
        if check.has_next():
            row = check.get_next()
            if row[0] and int(row[0]) > 0:
                _edge_cache.add(cache_key)
                return
    except Exception as exc:  # noqa: BLE001
        # If the check query fails (e.g. node type mismatch), fall through to CREATE
        logger.debug("Edge existence check failed, proceeding with CREATE: %s", exc)

    try:
        conn.execute(
            f"MATCH (a {{id: $from_id}}), (b {{id: $to_id}}) CREATE (a)-[:{edge_type}]->(b)",
            {"from_id": from_id, "to_id": to_id},
        )
        _edge_cache.add(cache_key)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Edge creation skipped (may already exist): %s", exc)


def record_agent_edit(
    conn: kuzu.Connection,
    project_id: str,
    node_id: str,
    task_id: str,
    agent: str,
) -> None:
    """Record an agent modification as an audit trail edge."""
    session_id = str(uuid.uuid4())
    from datetime import datetime

    conn.execute(
        """
        CREATE (s:AgentSession {
            id: $id,
            task_id: $task_id,
            project_id: $project_id,
            agent: $agent,
            created_at: $created_at
        })
        """,
        {
            "id": session_id,
            "task_id": task_id,
            "project_id": project_id,
            "agent": agent,
            "created_at": datetime.utcnow().isoformat(),
        },
    )
    add_edge(conn, "MODIFIED_BY_AGENT", node_id, session_id)


# ---------------------------------------------------------------------------
# Subgraph query
# ---------------------------------------------------------------------------


def get_subgraph(
    conn: kuzu.Connection,
    project_id: str,
    node_id: str,
    hops: int = 2,
) -> dict[str, Any]:
    """
    Return nodes and edges within N hops of a given node.
    Includes a token count estimate for Agent Bridge budget planning.
    """
    # Collect all node IDs reachable within `hops` hops
    visited: set[str] = set()
    frontier = {node_id}
    for _ in range(hops):
        next_frontier: set[str] = set()
        for nid in frontier:
            result = conn.execute(
                """
                MATCH (a {id: $id, project_id: $project_id})-[r]->(b)
                RETURN b.id AS bid
                UNION
                MATCH (a)-[r]->(b {id: $id, project_id: $project_id})
                RETURN a.id AS bid
                """,
                {"id": nid, "project_id": project_id},
            )
            while result.has_next():
                row = result.get_next()
                neighbour_id = row[0]
                if neighbour_id and neighbour_id not in visited:
                    next_frontier.add(neighbour_id)
        visited.update(frontier)
        frontier = next_frontier - visited
    visited.update(frontier)

    # Fetch full node properties for all visited IDs
    nodes = []
    for nid in visited:
        node_data = get_node(conn, project_id, nid)
        if node_data:
            nodes.append(node_data)

    # Fetch edges between visited nodes
    edges = []
    for nid in visited:
        result = conn.execute(
            """
            MATCH (a {id: $id})-[r]->(b)
            WHERE b.id IN $ids
            RETURN a.id, type(r), b.id
            """,
            {"id": nid, "ids": list(visited)},
        )
        while result.has_next():
            row = result.get_next()
            edges.append({"from_id": row[0], "edge_type": row[1], "to_id": row[2]})

    serialised = json.dumps({"nodes": nodes, "edges": edges})
    token_estimate = len(serialised) // _CHARS_PER_TOKEN

    return {"nodes": nodes, "edges": edges, "token_estimate": token_estimate}


def get_node(conn: kuzu.Connection, project_id: str, node_id: str) -> dict[str, Any] | None:
    """Return a single node with all its properties."""
    for node_type in ("File", "Function", "Class", "Import", "Variable", "EnvRef", "AgentSession"):
        try:
            result = conn.execute(
                f"MATCH (n:{node_type} {{id: $id, project_id: $project_id}}) RETURN n.*",
                {"id": node_id, "project_id": project_id},
            )
            if result.has_next():
                row = result.get_next()
                return {"id": node_id, "type": node_type, "properties": dict(zip(result.get_column_names(), row))}
        except Exception:  # noqa: BLE001
            continue
    return None


def search_nodes(conn: kuzu.Connection, project_id: str, query: str) -> list[dict[str, Any]]:
    """Full-text search across node names and docstrings (case-insensitive substring match)."""
    results = []
    q = query.lower()
    for node_type in ("Function", "Class", "File"):
        try:
            result = conn.execute(
                f"""
                MATCH (n:{node_type} {{project_id: $project_id}})
                WHERE lower(n.name) CONTAINS $query
                RETURN n.id, n.name
                LIMIT 20
                """,
                {"project_id": project_id, "query": q},
            )
            while result.has_next():
                row = result.get_next()
                results.append({"id": row[0], "type": node_type, "name": row[1]})
        except Exception:  # noqa: BLE001
            continue
    return results


def get_env_refs(conn: kuzu.Connection, project_id: str, key_name: str) -> list[dict[str, Any]]:
    """Return all nodes referencing a specific .env key name."""
    results = []
    try:
        result = conn.execute(
            """
            MATCH (e:EnvRef {project_id: $project_id, key_name: $key_name})
            RETURN e.id, e.file_path, e.line
            """,
            {"project_id": project_id, "key_name": key_name},
        )
        while result.has_next():
            row = result.get_next()
            results.append({"id": row[0], "file_path": row[1], "line": row[2]})
    except Exception:  # noqa: BLE001
        pass
    return results


def get_all_nodes(conn: kuzu.Connection, project_id: str) -> list[dict[str, Any]]:
    """
    Return all nodes for a project across all node types.
    Used by the Security Scanner to build the full graph for path traversal.
    Each returned dict has: id, type, properties.
    """
    all_nodes: list[dict[str, Any]] = []
    for node_type in ("File", "Function", "Class", "Import", "Variable", "EnvRef"):
        try:
            result = conn.execute(
                f"MATCH (n:{node_type} {{project_id: $project_id}}) RETURN n.id",
                {"project_id": project_id},
            )
            while result.has_next():
                row = result.get_next()
                node_id = row[0]
                node = get_node(conn, project_id, node_id)
                if node:
                    all_nodes.append(node)
        except Exception:  # noqa: BLE001
            continue
    return all_nodes


def get_all_edges(conn: kuzu.Connection, project_id: str) -> list[dict[str, Any]]:
    """
    Return all edges for a project as {from_id, to_id, edge_type} dicts.
    Used by the Security Scanner to build the adjacency map for traversal.
    Covers CALLS, IMPORTS, DEFINES, REFERENCES, and MODIFIED_BY_AGENT edges.
    """
    all_edges: list[dict[str, Any]] = []
    edge_types = ("CALLS", "IMPORTS", "DEFINES", "REFERENCES", "MODIFIED_BY_AGENT")
    for edge_type in edge_types:
        try:
            result = conn.execute(
                f"""
                MATCH (a)-[r:{edge_type}]->(b)
                WHERE a.project_id = $project_id
                RETURN a.id, b.id
                """,
                {"project_id": project_id},
            )
            while result.has_next():
                row = result.get_next()
                if row[0] and row[1]:
                    all_edges.append({
                        "from_id": row[0],
                        "to_id": row[1],
                        "edge_type": edge_type,
                    })
        except Exception:  # noqa: BLE001
            continue
    return all_edges


def get_call_chain(conn: kuzu.Connection, project_id: str, fn_id: str) -> dict[str, Any]:
    """Return the full call chain upstream and downstream of a function."""
    upstream: list[dict] = []
    downstream: list[dict] = []

    # Downstream: functions this function calls
    try:
        result = conn.execute(
            """
            MATCH (f:Function {id: $id, project_id: $project_id})-[:CALLS*1..5]->(callee:Function)
            RETURN callee.id, callee.name, callee.file_path
            """,
            {"id": fn_id, "project_id": project_id},
        )
        while result.has_next():
            row = result.get_next()
            downstream.append({"id": row[0], "name": row[1], "file_path": row[2]})
    except Exception:  # noqa: BLE001
        pass

    # Upstream: functions that call this function
    try:
        result = conn.execute(
            """
            MATCH (caller:Function {project_id: $project_id})-[:CALLS*1..5]->(f:Function {id: $id})
            RETURN caller.id, caller.name, caller.file_path
            """,
            {"id": fn_id, "project_id": project_id},
        )
        while result.has_next():
            row = result.get_next()
            upstream.append({"id": row[0], "name": row[1], "file_path": row[2]})
    except Exception:  # noqa: BLE001
        pass

    return {"fn_id": fn_id, "upstream": upstream, "downstream": downstream}
