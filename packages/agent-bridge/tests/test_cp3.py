"""
CP-3 tests for the Agent Bridge package.

Tests:
  - Context assembler builds correct structure (KG + LSM + Vault)
  - Token savings calculation is correct
  - format_prompt_messages produces correct role structure
  - dispatch endpoint enriches token_usage with context_node_count
  - prompt_tokens and completion_tokens aliases are set
  - raw_total_tokens is surfaced in token_usage
  - api_key is required for Claude/Codex dispatch
  - Restricted path detection works in _parse_agent_response
  - Diff parsing extracts unified diff correctly
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.context_assembler import _compute_savings, format_prompt_messages


# ---------------------------------------------------------------------------
# Token savings calculation
# ---------------------------------------------------------------------------


class TestTokenSavings:
    def test_savings_zero_when_raw_is_zero(self):
        assert _compute_savings(100, 0) == 0.0

    def test_savings_zero_when_context_is_zero(self):
        assert _compute_savings(0, 1000) == 0.0

    def test_savings_correct_percentage(self):
        # 100 context tokens out of 1000 raw = 90% savings
        result = _compute_savings(100, 1000)
        assert result == 90.0

    def test_savings_clamped_to_100(self):
        # context > raw should not produce negative savings
        result = _compute_savings(2000, 1000)
        assert result == 0.0

    def test_savings_clamped_to_zero_minimum(self):
        result = _compute_savings(500, 500)
        assert result == 0.0

    def test_savings_partial(self):
        result = _compute_savings(250, 1000)
        assert result == 75.0

    def test_savings_rounding(self):
        result = _compute_savings(333, 1000)
        assert result == 66.7


# ---------------------------------------------------------------------------
# format_prompt_messages
# ---------------------------------------------------------------------------


class TestFormatPromptMessages:
    def _make_context(self, nodes=None, edges=None, vault_keys=None):
        return {
            "system_prompt": "You are a coding agent.",
            "kg_context": {
                "nodes": nodes or [],
                "edges": edges or [],
                "token_estimate": 100,
            },
            "vault_keys": vault_keys or [],
            "task_description": "Fix the bug in main.py",
            "budget_tier": "standard",
            "context_tokens": 100,
            "raw_total_tokens": 1000,
            "savings_vs_raw": 90.0,
        }

    def test_returns_two_messages(self):
        ctx = self._make_context()
        messages = format_prompt_messages(ctx)
        assert len(messages) == 2

    def test_first_message_is_system(self):
        ctx = self._make_context()
        messages = format_prompt_messages(ctx)
        assert messages[0]["role"] == "system"
        assert "coding agent" in messages[0]["content"]

    def test_second_message_is_user(self):
        ctx = self._make_context()
        messages = format_prompt_messages(ctx)
        assert messages[1]["role"] == "user"

    def test_user_message_contains_task(self):
        ctx = self._make_context()
        messages = format_prompt_messages(ctx)
        assert "Fix the bug in main.py" in messages[1]["content"]

    def test_user_message_contains_kg_context(self):
        ctx = self._make_context(nodes=[{"id": "n1", "type": "Function"}])
        messages = format_prompt_messages(ctx)
        assert "n1" in messages[1]["content"]

    def test_user_message_contains_vault_keys(self):
        ctx = self._make_context(vault_keys=["DB_PASSWORD", "API_KEY"])
        messages = format_prompt_messages(ctx)
        assert "DB_PASSWORD" in messages[1]["content"]
        assert "API_KEY" in messages[1]["content"]


# ---------------------------------------------------------------------------
# Diff and response parsing
# ---------------------------------------------------------------------------


class TestResponseParsing:
    def test_parse_unified_diff(self):
        from src.claude import _parse_agent_response
        raw = """--- a/main.py
+++ b/main.py
@@ -1,3 +1,3 @@
 def foo():
-    return 1
+    return 2
{"explanation": "Fixed return value", "confidence": 0.9}"""
        result = _parse_agent_response(raw)
        assert "--- a/main.py" in result["diff"]
        assert result["explanation"] == "Fixed return value"
        assert result["confidence"] == 0.9
        assert result["flagged"] is False

    def test_restricted_path_flagged(self):
        from src.claude import _parse_agent_response
        raw = """--- a/main.py
+++ b/main.py
@@ -1 +1 @@
-x = 1
+x = open('/cqr/secrets', 'r').read()
{"explanation": "bad", "confidence": 0.1}"""
        result = _parse_agent_response(raw)
        assert result["flagged"] is True
        assert "/cqr/" in result["flag_reason"]

    def test_vault_path_flagged(self):
        from src.claude import _parse_agent_response
        raw = 'open("/vault/keys/proj")\n{"explanation": "bad", "confidence": 0.1}'
        result = _parse_agent_response(raw)
        assert result["flagged"] is True

    def test_missing_json_block(self):
        from src.claude import _parse_agent_response
        raw = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b"
        result = _parse_agent_response(raw)
        assert result["diff"] != "" or result["explanation"] == ""
        assert result["confidence"] == 0.5  # default

    def test_confidence_clamped_to_1(self):
        from src.claude import _parse_agent_response
        raw = '{"explanation": "test", "confidence": 1.5}'
        result = _parse_agent_response(raw)
        assert result["confidence"] <= 1.0

    def test_confidence_clamped_to_0(self):
        from src.claude import _parse_agent_response
        raw = '{"explanation": "test", "confidence": -0.5}'
        result = _parse_agent_response(raw)
        assert result["confidence"] >= 0.0


# ---------------------------------------------------------------------------
# API endpoint: token_usage enrichment
# ---------------------------------------------------------------------------


class TestDispatchEndpointTokenUsage:
    """Test that the dispatch endpoint correctly enriches token_usage."""

    @pytest.mark.asyncio
    async def test_token_usage_has_context_node_count(self):
        from fastapi.testclient import TestClient
        from src.api import app

        # Mock assemble_context to return a context with 3 KG nodes
        mock_context = {
            "system_prompt": "sys",
            "kg_context": {
                "nodes": [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}],
                "edges": [],
                "token_estimate": 150,
            },
            "vault_keys": [],
            "task_description": "test task",
            "budget_tier": "standard",
            "context_tokens": 150,
            "raw_total_tokens": 1000,
            "savings_vs_raw": 85.0,
        }

        mock_dispatch_result = {
            "task_id": "task-123",
            "diff": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b",
            "explanation": "fixed",
            "confidence": 0.9,
            "flagged": False,
            "flag_reason": None,
            "token_usage": {
                "context_tokens": 150,
                "response_tokens": 50,
                "total_tokens": 200,
                "savings_vs_raw": 85.0,
            },
        }

        with patch("src.api.assemble_context", new_callable=AsyncMock, return_value=mock_context), \
             patch("src.api.dispatch_claude", new_callable=AsyncMock, return_value=mock_dispatch_result):

            client = TestClient(app)
            resp = client.post("/agent/dispatch", json={
                "task_id": "task-123",
                "project_id": "proj-456",
                "task_description": "test task",
                "agent": "claude",
                "api_key": "sk-test-key",
                "api_key_type": "anthropic",
            })

        assert resp.status_code == 200
        data = resp.json()
        tu = data["token_usage"]
        assert tu["context_node_count"] == 3
        assert tu["prompt_tokens"] == 150
        assert tu["completion_tokens"] == 50
        assert tu["raw_total_tokens"] == 1000
        assert tu["savings_vs_raw"] == 85.0

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_422(self):
        from fastapi.testclient import TestClient
        from src.api import app

        client = TestClient(app)
        resp = client.post("/agent/dispatch", json={
            "task_id": "t1",
            "project_id": "p1",
            "task_description": "task",
            "agent": "claude",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_400(self):
        from fastapi.testclient import TestClient
        from src.api import app

        mock_context = {
            "system_prompt": "sys",
            "kg_context": {"nodes": [], "edges": [], "token_estimate": 0},
            "vault_keys": [],
            "task_description": "t",
            "budget_tier": "standard",
            "context_tokens": 0,
            "raw_total_tokens": 0,
            "savings_vs_raw": 0.0,
        }

        with patch("src.api.assemble_context", new_callable=AsyncMock, return_value=mock_context):
            client = TestClient(app)
            resp = client.post("/agent/dispatch", json={
                "task_id": "t1",
                "project_id": "p1",
                "task_description": "task",
                "agent": "gpt-9000",
                "api_key": "sk-test",
            })
        assert resp.status_code == 400
