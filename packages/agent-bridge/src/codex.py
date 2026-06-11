"""
Codex (OpenAI) LLM dispatcher for the Agent Bridge.

API key policy:
  The OpenAI API key is NEVER read from environment variables here.
  It is accepted as a parameter at call time, used to instantiate the SDK
  client for that single call, and then garbage-collected. It is never
  assigned to a module-level variable and never logged.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-4o")
_MAX_TOKENS = int(os.getenv("CODEX_MAX_TOKENS", "4096"))

_RESTRICTED_PATHS = ["/cqr/", "/vault/", ".env.keys"]


def _detect_restricted_path(text: str) -> tuple[bool, str]:
    """Check if agent output references restricted paths."""
    for path in _RESTRICTED_PATHS:
        if path in text:
            return True, f"Output references restricted path: {path}"
    return False, ""


def _parse_agent_response(raw: str) -> dict[str, Any]:
    """Parse agent output into structured diff + explanation."""
    diff_pattern = re.compile(r"(---\s+\S+.*?(?=\{\"explanation\"|$))", re.DOTALL)
    diff_match = diff_pattern.search(raw)
    diff = diff_match.group(1).strip() if diff_match else ""

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


async def dispatch_codex(
    messages: list[dict[str, str]],
    task_id: str,
    api_key: str,
    savings_vs_raw: float = 0.0,
) -> dict[str, Any]:
    """
    Dispatch a prompt to OpenAI (GPT-4o) and return a structured AgentResponse dict.

    Parameters
    ----------
    messages:
        The prompt messages to send to the model.
    task_id:
        The CQR task ID (for logging — never the key).
    api_key:
        The user-supplied OpenAI API key. Instantiated into the SDK
        client for this call only. Never stored or logged.
    savings_vs_raw:
        Pre-computed token savings percentage from the context assembler.
    """
    from openai import AsyncOpenAI

    # Key is used here and only here — client is local to this call frame
    client = AsyncOpenAI(api_key=api_key)

    try:
        response = await client.chat.completions.create(
            model=_CODEX_MODEL,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=_MAX_TOKENS,
        )
        raw_text = response.choices[0].message.content or ""
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

    except Exception as exc:  # noqa: BLE001
        logger.error("Codex dispatch failed for task %s: %s", task_id, exc)
        return {
            "task_id": task_id,
            "diff": "",
            "explanation": f"Codex dispatch error: {exc}",
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
            "savings_vs_raw": savings_vs_raw,
        },
    }
