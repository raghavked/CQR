"""
pgvector spatial index management for the LSM proximity layer.
Handles vector storage, ivfflat index creation, and ANN search.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector

from ..embeddings.embedder import embedding_dim

logger = logging.getLogger(__name__)

_DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://cqr:cqr@localhost:5432/cqr")

# Proximity threshold — nodes below this score are excluded from agent context
PROXIMITY_THRESHOLD = 0.70

# Token budget tiers (max tokens per tier)
BUDGET_TIERS: dict[str, int] = {
    "micro": 2_000,
    "standard": 8_000,
    "extended": 32_000,
    "uncapped": 128_000,
}

# Approximate chars-per-token for budget estimation
_CHARS_PER_TOKEN = 4


def get_conn() -> psycopg2.extensions.connection:
    """Open a psycopg2 connection with pgvector registered."""
    conn = psycopg2.connect(_DATABASE_URL)
    register_vector(conn)
    return conn


def ensure_schema() -> None:
    """Create the lsm_embeddings table and ivfflat index if they do not exist."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS lsm_embeddings (
                id SERIAL PRIMARY KEY,
                node_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                node_type TEXT,
                snippet TEXT,
                embedding vector({embedding_dim()})
            )
            """
        )
        # ivfflat index for approximate nearest-neighbour search
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS lsm_ivfflat_idx
            ON lsm_embeddings
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
            """
        )
    conn.commit()
    conn.close()


def upsert_embedding(
    node_id: str,
    project_id: str,
    node_type: str,
    snippet: str,
    embedding: list[float],
) -> None:
    """Insert or update a node's embedding in the pgvector table."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO lsm_embeddings (node_id, project_id, node_type, snippet, embedding)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (node_id) DO UPDATE
            SET project_id = EXCLUDED.project_id,
                node_type = EXCLUDED.node_type,
                snippet = EXCLUDED.snippet,
                embedding = EXCLUDED.embedding
            """,
            (node_id, project_id, node_type, snippet, embedding),
        )
    conn.commit()
    conn.close()


def delete_embedding(node_id: str) -> None:
    """Remove a node's embedding from the pgvector table."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM lsm_embeddings WHERE node_id = %s", (node_id,))
    conn.commit()
    conn.close()


def proximity_search(
    project_id: str,
    query_embedding: list[float],
    limit: int = 50,
    threshold: float = PROXIMITY_THRESHOLD,
) -> list[dict[str, Any]]:
    """
    Return nodes ranked by cosine similarity to the query embedding.
    Only nodes with similarity >= threshold are returned.
    """
    conn = get_conn()
    results = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT node_id, node_type, snippet,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM lsm_embeddings
            WHERE project_id = %s
              AND 1 - (embedding <=> %s::vector) >= %s
            ORDER BY similarity DESC
            LIMIT %s
            """,
            (query_embedding, project_id, query_embedding, threshold, limit),
        )
        for row in cur.fetchall():
            results.append(
                {
                    "node_id": row[0],
                    "node_type": row[1],
                    "snippet": row[2],
                    "proximity_score": float(row[3]),
                }
            )
    conn.close()
    return results


def budget_plan(
    project_id: str,
    query_embedding: list[float],
    budget_tier: str = "standard",
) -> list[dict[str, Any]]:
    """
    Return the optimal set of nodes that fit within the token budget,
    ranked by proximity score descending.
    """
    max_tokens = BUDGET_TIERS.get(budget_tier, BUDGET_TIERS["standard"])
    candidates = proximity_search(project_id, query_embedding, limit=200)

    selected: list[dict[str, Any]] = []
    used_tokens = 0

    for node in candidates:
        snippet = node.get("snippet", "")
        node_tokens = len(snippet) // _CHARS_PER_TOKEN
        if used_tokens + node_tokens <= max_tokens:
            selected.append(node)
            used_tokens += node_tokens
        if used_tokens >= max_tokens:
            break

    return selected
