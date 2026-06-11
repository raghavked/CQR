"""
Agent Bridge internal FastAPI router.
Assembles context and dispatches to Claude or Codex.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .claude import dispatch_claude
from .codex import dispatch_codex
from .context_assembler import assemble_context, format_prompt_messages

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CQR Agent Bridge",
    description="LLM dispatcher with KG/LSM context assembly (internal).",
    version="0.1.0",
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Return service health status."""
    return {"status": "ok", "service": "agent-bridge"}


@app.post("/agent/dispatch", tags=["agent"])
async def dispatch(body: DispatchRequest) -> dict[str, Any]:
    """
    Assemble context from KG + LSM + Vault and dispatch to the selected LLM.
    Returns a structured AgentResponse with diff, explanation, confidence, and token usage.
    """
    # 1. Assemble context
    context = await assemble_context(
        task_description=body.task_description,
        project_id=body.project_id,
        budget_tier=body.budget_tier,
    )

    # 2. Format messages
    messages = format_prompt_messages(context)

    # 3. Dispatch to selected LLM
    agent = body.agent.lower()
    if agent == "claude":
        result = await dispatch_claude(messages, body.task_id)
    elif agent in ("codex", "openai", "gpt"):
        result = await dispatch_codex(messages, body.task_id)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {body.agent}")

    # 4. Flag rejected responses
    if result.get("flagged"):
        logger.warning(
            '{"event": "agent_response_flagged", "task_id": "%s", "reason": "%s"}',
            body.task_id,
            result.get("flag_reason"),
        )

    return result
