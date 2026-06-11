"""
Claude (Anthropic) LLM dispatcher for the Agent Bridge.
Supports streaming and structured response parsing.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)

_CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "4096"))

# Paths that agents must never reference in their output
_RESTRICTED_PATHS = ["/cqr/", "/vault/", ".env.keys"]


def _detect_restricted_path(text: str) -> tuple[bool, str]:
    """Check if agent output references restricted paths."""
    for path in _RESTRICTED_PATHS:
        if path in text:
            return True, f"Output references restricted path: {path}"
    return False, ""


def _parse_agent_response(raw: str) -> dict[str, Any]:
    """
    Parse agent output into structured diff + explanation.
    Agent is instructed to output: unified diff + JSON block.
    """
    # Extract unified diff blocks
    diff_pattern = re.compile(r"(---\s+\S+.*?(?=\{\"explanation\"|$))", re.DOTALL)
    diff_match = diff_pattern.search(raw)
    diff = diff_match.group(1).strip() if diff_match else ""

    # Extract JSON explanation block
    json_pattern = re.compile(r'\{[^{}]*"explanation"[^{}]*\}', re.DOTALL)
    json_match = json_pattern.search(raw)
    explanation = ""
    confidence = 0.5

    if json_match:
        try:
            parsed = json.loads(json_match.group(0))
            explanation = parsed.get("explanation", "")
            confidence = float(parsed.get("confidence", 0.5))
        except json.JSONDecodeError:
            explanation = "Could not parse explanation JSON"

    flagged, flag_reason = _detect_restricted_path(raw)

    return {
        "diff": diff,
        "explanation": explanation,
        "confidence": max(0.0, min(1.0, confidence)),
        "flagged": flagged,
        "flag_reason": flag_reason if flagged else None,
    }


async def dispatch_claude(
    messages: list[dict[str, str]],
    task_id: str,
) -> dict[str, Any]:
    """
    Dispatch a prompt to Claude and return a structured AgentResponse dict.
    """
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    system_message = next((m["content"] for m in messages if m["role"] == "system"), "")
    user_messages = [m for m in messages if m["role"] != "system"]

    try:
        response = await client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_message,
            messages=user_messages,  # type: ignore[arg-type]
        )
        raw_text = response.content[0].text if response.content else ""
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

    except Exception as exc:  # noqa: BLE001
        logger.error("Claude dispatch failed for task %s: %s", task_id, exc)
        return {
            "task_id": task_id,
            "diff": "",
            "explanation": f"Claude dispatch error: {exc}",
            "confidence": 0.0,
            "flagged": False,
            "flag_reason": None,
            "token_usage": {
                "context_tokens": 0,
                "response_tokens": 0,
                "total_tokens": 0,
                "savings_vs_raw": 0.0,
            },
        }

    parsed = _parse_agent_response(raw_text)
    return {
        "task_id": task_id,
        **parsed,
        "token_usage": {
            "context_tokens": input_tokens,
            "response_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "savings_vs_raw": 0.0,  # TODO(AMBIGUITY): compute actual savings vs raw file send
        },
    }
