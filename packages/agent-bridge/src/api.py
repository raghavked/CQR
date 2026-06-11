"""
Agent Bridge internal FastAPI router.

Assembles context from KG + LSM + Vault and dispatches to Claude or Codex.

API key policy:
  api_key flows in from the Orchestration layer via the request body.
  It is passed directly to the dispatcher (dispatch_claude / dispatch_codex)
  and used only to instantiate the SDK client for that call.
  It is never stored, never logged, and never forwarded to any other package.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .claude import dispatch_claude
from .codex import dispatch_codex
from .context_assembler import assemble_context, format_prompt_messages

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CQR Agent Bridge",
    description="LLM dispatcher with KG/LSM context assembly (internal).",
    version="0.2.0",
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class DispatchRequest(BaseModel):
    """Request body for POST /agent/dispatch."""

    task_id: str
    project_id: str
    task_description: str
    agent: str = "claude"  # "claude" | "codex"
    budget_tier: str = "standard"
    # User-supplied API key — forwarded from Orchestration layer.
    # Never stored, never logged, never forwarded to KG/LSM/Vault.
    api_key: str | None = None
    api_key_type: Literal["anthropic", "openai"] | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Return service health status."""
    return {"status": "ok", "service": "agent-bridge", "version": "0.2.0"}


@app.post("/agent/dispatch", tags=["agent"])
async def dispatch(body: DispatchRequest) -> dict[str, Any]:
    """
    Assemble context from KG + LSM + Vault and dispatch to the selected LLM.

    The api_key in the request body is used ONLY to instantiate the SDK client
    for this single call. It is not stored, not logged, and not forwarded to
    any other internal service.

    Returns a structured AgentResponse with diff, explanation, confidence,
    token usage (including savings_vs_raw), and flagged status.
    """
    # Validate that a key was supplied
    agent = body.agent.lower()
    if agent == "claude" and not body.api_key:
        raise HTTPException(
            status_code=422,
            detail="api_key is required for Claude dispatch. Supply your Anthropic API key.",
        )
    if agent in ("codex", "openai", "gpt") and not body.api_key:
        raise HTTPException(
            status_code=422,
            detail="api_key is required for Codex dispatch. Supply your OpenAI API key.",
        )

    # 1. Assemble context (KG + LSM + Vault — no api_key involved)
    context = await assemble_context(
        task_description=body.task_description,
        project_id=body.project_id,
        budget_tier=body.budget_tier,
    )

    # 2. Format messages
    messages = format_prompt_messages(context)

    # 3. Extract context metadata for token usage enrichment
    savings_vs_raw: float = context.get("savings_vs_raw", 0.0)
    context_node_count: int = len(context.get("kg_context", {}).get("nodes", []))

    # 4. Dispatch to selected LLM — api_key used here and destroyed after call
    if agent == "claude":
        result = await dispatch_claude(
            messages,
            body.task_id,
            api_key=body.api_key,  # type: ignore[arg-type]
            savings_vs_raw=savings_vs_raw,
        )
    elif agent in ("codex", "openai", "gpt"):
        result = await dispatch_codex(
            messages,
            body.task_id,
            api_key=body.api_key,  # type: ignore[arg-type]
            savings_vs_raw=savings_vs_raw,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {body.agent}")

    # 5. Enrich token_usage with context_node_count and prompt/completion aliases
    if isinstance(result.get("token_usage"), dict):
        tu = result["token_usage"]
        tu["context_node_count"] = context_node_count
        tu["prompt_tokens"] = tu.get("context_tokens", 0)
        tu["completion_tokens"] = tu.get("response_tokens", 0)
        # Also surface raw_total_tokens and context_tokens from assembler
        tu["raw_total_tokens"] = context.get("raw_total_tokens", 0)
        result["token_usage"] = tu

    # 6. Flag rejected responses
    if result.get("flagged"):
        logger.warning(
            '{"event": "agent_response_flagged", "task_id": "%s", "reason": "%s"}',
            body.task_id,
            result.get("flag_reason"),
        )

    return result
