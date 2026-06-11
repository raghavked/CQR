"""
Kuzu graph database schema definition and connection management for the KG engine.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import kuzu

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema DDL — Kuzu Cypher CREATE TABLE statements
# ---------------------------------------------------------------------------

NODE_SCHEMAS = [
    """
    CREATE NODE TABLE IF NOT EXISTS File(
        id STRING,
        path STRING,
        language STRING,
        last_modified DOUBLE,
        hash STRING,
        project_id STRING,
        PRIMARY KEY (id)
    )
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS Function(
        id STRING,
        name STRING,
        file_path STRING,
        start_line INT64,
        end_line INT64,
        signature STRING,
        docstring STRING,
        project_id STRING,
        PRIMARY KEY (id)
    )
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS Class(
        id STRING,
        name STRING,
        file_path STRING,
        start_line INT64,
        end_line INT64,
        base_classes STRING,
        project_id STRING,
        PRIMARY KEY (id)
    )
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS Import(
        id STRING,
        source_file STRING,
        imported_module STRING,
        imported_names STRING,
        project_id STRING,
        PRIMARY KEY (id)
    )
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS Variable(
        id STRING,
        name STRING,
        file_path STRING,
        line INT64,
        type_annotation STRING,
        project_id STRING,
        PRIMARY KEY (id)
    )
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS EnvRef(
        id STRING,
        key_name STRING,
        file_path STRING,
        line INT64,
        project_id STRING,
        PRIMARY KEY (id)
    )
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS AgentSession(
        id STRING,
        task_id STRING,
        project_id STRING,
        agent STRING,
        created_at STRING,
        PRIMARY KEY (id)
    )
    """,
]

EDGE_SCHEMAS = [
    "CREATE REL TABLE IF NOT EXISTS CONTAINS(FROM File TO Function, FROM File TO Class, FROM File TO Variable)",
    "CREATE REL TABLE IF NOT EXISTS CALLS(FROM Function TO Function)",
    "CREATE REL TABLE IF NOT EXISTS IMPORTS(FROM File TO File)",
    "CREATE REL TABLE IF NOT EXISTS INHERITS(FROM Class TO Class)",
    "CREATE REL TABLE IF NOT EXISTS REFERENCES_ENV(FROM Function TO EnvRef)",
    "CREATE REL TABLE IF NOT EXISTS MODIFIED_BY_AGENT(FROM Function TO AgentSession, FROM File TO AgentSession)",
]


# ---------------------------------------------------------------------------
# Database connection factory
# ---------------------------------------------------------------------------


def get_db_path(project_id: str) -> Path:
    """Return the filesystem path for a project's Kuzu database."""
    base = os.getenv("KG_DB_BASE_PATH", "/tmp/cqr-kg")
    db_path = Path(base) / project_id
    db_path.mkdir(parents=True, exist_ok=True)
    return db_path


def get_connection(project_id: str) -> kuzu.Connection:
    """Open (or create) a Kuzu database connection for a project."""
    db_path = get_db_path(project_id)
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: kuzu.Connection) -> None:
    """Create node and edge tables if they do not already exist."""
    for ddl in NODE_SCHEMAS:
        try:
            conn.execute(ddl)
        except Exception as exc:  # noqa: BLE001
            # Table already exists — Kuzu raises on duplicate CREATE
            logger.debug("Schema DDL skipped (likely exists): %s", exc)
    for ddl in EDGE_SCHEMAS:
        try:
            conn.execute(ddl)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Schema DDL skipped (likely exists): %s", exc)
