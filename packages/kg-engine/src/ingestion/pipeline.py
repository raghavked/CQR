"""
Ingestion pipeline: walks a project directory, parses each file,
and upserts nodes/edges into the Kuzu Knowledge Graph.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..graph.crud import (
    add_edge,
    clear_edge_cache,
    upsert_class_node,
    upsert_env_ref_node,
    upsert_file_node,
    upsert_function_node,
    upsert_import_node,
)
from ..graph.schema import get_connection
from .parser import detect_language, parse_file

logger = logging.getLogger(__name__)

# Files/directories to skip during ingestion
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".next"}
_MAX_FILE_SIZE_BYTES = 512 * 1024  # 512 KB — skip very large generated files


def ingest_project(project_id: str, repo_path: str) -> dict[str, Any]:
    """
    Ingest all supported source files in a project directory into the KG.
    Returns a summary of nodes created/updated.

    The edge cache is cleared at the start of each run so that re-ingestion
    correctly detects existing edges in the DB rather than skipping them.
    """
    conn = get_connection(project_id)
    clear_edge_cache()  # Reset per-run deduplication cache
    summary = {"files": 0, "functions": 0, "classes": 0, "imports": 0, "env_refs": 0, "errors": 0}
    changed_node_ids: list[str] = []

    repo = Path(repo_path)
    if not repo.exists():
        logger.warning("Repo path does not exist: %s", repo_path)
        return summary

    for root, dirs, files in os.walk(repo):
        # Prune skipped directories in-place
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for filename in files:
            file_path = os.path.join(root, filename)

            if not detect_language(file_path):
                continue

            if os.path.getsize(file_path) > _MAX_FILE_SIZE_BYTES:
                logger.debug("Skipping large file: %s", file_path)
                continue

            structure = parse_file(file_path)
            if not structure:
                continue

            try:
                # Read raw content for token count calculation
                try:
                    raw_content = Path(file_path).read_text(encoding="utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    raw_content = ""

                meta = structure["file_meta"]
                # Pass raw content so upsert_file_node can compute raw_token_count
                meta["content"] = raw_content
                file_node_id = upsert_file_node(conn, project_id, meta)
                changed_node_ids.append(file_node_id)
                summary["files"] += 1

                # Functions
                for fn in structure.get("functions", []):
                    fn_id = upsert_function_node(conn, project_id, fn)
                    add_edge(conn, "CONTAINS", file_node_id, fn_id)
                    changed_node_ids.append(fn_id)
                    summary["functions"] += 1

                # Classes
                for cls in structure.get("classes", []):
                    cls_id = upsert_class_node(conn, project_id, cls)
                    add_edge(conn, "CONTAINS", file_node_id, cls_id)
                    changed_node_ids.append(cls_id)
                    summary["classes"] += 1

                # Imports
                for imp in structure.get("imports", []):
                    imp_id = upsert_import_node(conn, project_id, imp)
                    summary["imports"] += 1

                # Env refs — also create REFERENCES_ENV edges from enclosing function if known
                for ref in structure.get("env_refs", []):
                    ref_id = upsert_env_ref_node(conn, project_id, ref)
                    changed_node_ids.append(ref_id)
                    summary["env_refs"] += 1
                    # Link the file to the env ref for structural queries
                    add_edge(conn, "CONTAINS", file_node_id, ref_id)

                # Build a name→id lookup for functions in this file for CALLS edges
                fn_name_to_id: dict[str, str] = {}
                for fn in structure.get("functions", []):
                    from ..graph.crud import _node_id as _nid
                    fid = _nid("Function", project_id, fn["file_path"], fn["name"], str(fn.get("start_line", 0)))
                    fn_name_to_id[fn["name"]] = fid

                # Call sites — create CALLS edges between Function nodes
                # For cross-file calls, we create a synthetic sink Function node
                # representing the callee by name (for taint-flow traversal)
                for cs in structure.get("call_sites", []):
                    caller_id = fn_name_to_id.get(cs["caller_name"])
                    if not caller_id:
                        continue
                    callee_name = cs["callee_name"]
                    callee_full = cs.get("callee_full", callee_name)
                    # Look up callee in same file first
                    callee_id = fn_name_to_id.get(callee_name)
                    if not callee_id:
                        # Create a synthetic Function node for the callee
                        # (captures stdlib/external calls like cursor.execute)
                        synthetic_fn = {
                            "name": callee_name,
                            "file_path": file_path,
                            "start_line": cs.get("line", 0),
                            "end_line": cs.get("line", 0),
                            "signature": callee_full,
                            "docstring": "",
                        }
                        callee_id = upsert_function_node(conn, project_id, synthetic_fn)
                    add_edge(conn, "CALLS", caller_id, callee_id)

            except Exception as exc:  # noqa: BLE001
                logger.error("Error ingesting file %s: %s", file_path, exc)
                summary["errors"] += 1

    logger.info(
        '{"event": "kg_ingested", "project_id": "%s", "summary": %s}',
        project_id,
        summary,
    )
    return {**summary, "changed_node_ids": changed_node_ids}
