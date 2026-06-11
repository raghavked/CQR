"""
Context assembler for the Agent Bridge.

Composes KG subgraphs and LSM proximity data into a structured LLM prompt.
Calculates real token savings vs. sending all raw files (Critical Fix 2).

Token savings formula (PDR §8.3):
  raw_total_tokens  = sum of raw_token_count on all File nodes in the project
  context_tokens    = token_estimate returned by the KG subgraph queries
  savings_vs_raw    = round((1 - context_tokens / raw_total_tokens) * 100, 1)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

KG_ENGINE_URL = os.getenv("KG_ENGINE_URL", "http://localhost:8001")
LSM_LAYER_URL = os.getenv("LSM_LAYER_URL", "http://localhost:8002")
VAULT_URL = os.getenv("VAULT_URL", "http://localhost:8004")

# System prompt template for agent dispatch
_SYSTEM_PROMPT = """You are a coding agent operating inside a CQR sandboxed environment.

You have been given:
1. A structured Knowledge Graph (KG) context showing relevant code nodes and their relationships.
2. A list of available environment variable key names (no values — they are injected at runtime).
3. A task description.

Your output MUST be a unified diff only. Do not include explanations outside the diff.
Format:
--- a/path/to/file
+++ b/path/to/file
@@ ... @@
 context line
-removed line
+added line

Rules:
- Only modify files referenced in the KG context or explicitly mentioned in the task.
- Do NOT read or write /cqr/ paths.
- Do NOT attempt to access vault secret values — use key names only.
- Do NOT include hardcoded credentials or secrets in your diff.
- After the diff, include a JSON block: {"explanation": "...", "confidence": 0.0-1.0}
"""

# Characters per token approximation (standard: 1 token ≈ 4 chars)
_CHARS_PER_TOKEN = 4


async def _fetch_raw_total_tokens(client: httpx.AsyncClient, project_id: str) -> int:
    """
    Fetch all File nodes for a project and sum their raw_token_count properties.
    Falls back to 0 if the KG is unreachable.

    raw_token_count is stored on File nodes during ingestion (KG schema fix).
    If a File node does not have raw_token_count set, it is skipped (treated as 0).
    """
    try:
        response = await client.get(
            f"{KG_ENGINE_URL}/kg/nodes",
            params={"project_id": project_id},
        )
        response.raise_for_status()
        nodes = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not fetch nodes for token savings calc: %s", exc)
        return 0

    total = 0
    for node in nodes:
        if node.get("type") != "File":
            continue
        props = node.get("properties", {})
        raw_count = props.get("raw_token_count")
        if raw_count is not None:
            try:
                total += int(raw_count)
            except (TypeError, ValueError):
                pass
    return total


def _compute_savings(context_tokens: int, raw_total_tokens: int) -> float:
    """
    Compute the percentage of tokens saved vs. sending all raw files.
    Returns 0.0 if raw_total_tokens is 0 (avoids division by zero).
    Clamps to [0.0, 100.0].
    """
    if raw_total_tokens <= 0 or context_tokens <= 0:
        return 0.0
    savings = round((1 - context_tokens / raw_total_tokens) * 100, 1)
    return max(0.0, min(100.0, savings))


async def assemble_context(
    task_description: str,
    project_id: str,
    budget_tier: str = "standard",
) -> dict[str, Any]:
    """
    Assemble the full LLM context for a task.

    Steps:
      1. Get budget-optimal node set from LSM
      2. Fetch subgraphs from KG for each node
      3. Fetch raw_total_tokens from all File nodes (for savings calculation)
      4. Retrieve vault key names
      5. Compute savings_vs_raw
      6. Serialise and return context
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Get budget plan from LSM
        try:
            lsm_response = await client.get(
                f"{LSM_LAYER_URL}/lsm/budget-plan",
                params={
                    "project_id": project_id,
                    "task": task_description,
                    "budget_tier": budget_tier,
                },
            )
            lsm_response.raise_for_status()
            budget_nodes = lsm_response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("LSM budget-plan failed: %s — using empty context", exc)
            budget_nodes = []

        # 2. Fetch KG subgraphs for each node in the budget plan
        all_nodes: dict[str, Any] = {}
        all_edges: list[dict] = []
        context_tokens = 0

        for node_info in budget_nodes[:20]:  # Cap at 20 nodes to avoid runaway fetches
            node_id = node_info.get("node_id")
            if not node_id:
                continue
            try:
                kg_response = await client.get(
                    f"{KG_ENGINE_URL}/kg/subgraph",
                    params={"project_id": project_id, "node_id": node_id, "hops": 1},
                )
                kg_response.raise_for_status()
                subgraph = kg_response.json()
                for n in subgraph.get("nodes", []):
                    all_nodes[n["id"]] = n
                all_edges.extend(subgraph.get("edges", []))
                context_tokens += subgraph.get("token_estimate", 0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("KG subgraph fetch failed for node %s: %s", node_id, exc)

        # 3. Fetch raw_total_tokens from all File nodes
        raw_total_tokens = await _fetch_raw_total_tokens(client, project_id)

        # 4. Retrieve vault key names
        vault_keys: list[str] = []
        try:
            vault_response = await client.get(f"{VAULT_URL}/vault/keys/{project_id}")
            vault_response.raise_for_status()
            vault_keys = vault_response.json().get("keys", [])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Vault key list failed: %s", exc)

    # 5. Compute token savings
    savings_vs_raw = _compute_savings(context_tokens, raw_total_tokens)
    if savings_vs_raw > 0:
        logger.info(
            '{"event": "context_assembled", "project_id": "%s", "context_tokens": %d, '
            '"raw_total_tokens": %d, "savings_vs_raw": %.1f}',
            project_id,
            context_tokens,
            raw_total_tokens,
            savings_vs_raw,
        )

    # 6. Serialise context
    kg_context = {
        "nodes": list(all_nodes.values()),
        "edges": all_edges,
        "token_estimate": context_tokens,
    }

    return {
        "system_prompt": _SYSTEM_PROMPT,
        "kg_context": kg_context,
        "vault_keys": vault_keys,
        "task_description": task_description,
        "budget_tier": budget_tier,
        "context_tokens": context_tokens,
        "raw_total_tokens": raw_total_tokens,
        "savings_vs_raw": savings_vs_raw,
    }


def format_prompt_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    """
    Format the assembled context into a list of chat messages for LLM dispatch.
    Returns [{"role": "system", "content": ...}, {"role": "user", "content": ...}]
    """
    kg_json = json.dumps(context["kg_context"], indent=2)
    vault_keys = context["vault_keys"]
    task = context["task_description"]

    user_content = f"""## Knowledge Graph Context
```json
{kg_json}
```

## Available Environment Variable Keys
{json.dumps(vault_keys)}

## Task
{task}

Output a unified diff only, followed by the JSON explanation block."""

    return [
        {"role": "system", "content": context["system_prompt"]},
        {"role": "user", "content": user_content},
    ]
