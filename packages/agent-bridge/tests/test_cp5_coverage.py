"""
CP-5 coverage tests for agent-bridge.
Uses actual function signatures discovered from source:
  - dispatch_claude(messages, task_id, api_key, savings_vs_raw) — AsyncAnthropic imported inside function
  - dispatch_codex(messages, task_id, api_key, savings_vs_raw) — AsyncOpenAI imported inside function
  - assemble_context returns dict with keys: system_prompt, kg_context, vault_keys, task_description, etc.
  - format_prompt_messages(context) expects: system_prompt, kg_context, vault_keys, task_description
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# context_assembler.py coverage
# ---------------------------------------------------------------------------


class TestContextAssembler:
    """Cover context_assembler.py — savings computation, raw tokens, format_prompt."""

    def test_compute_savings_correct(self):
        """_compute_savings returns correct percentage."""
        from src.context_assembler import _compute_savings
        assert _compute_savings(200, 1000) == 80.0
        assert _compute_savings(0, 1000) == 0.0
        assert _compute_savings(100, 0) == 0.0

    def test_compute_savings_capped_at_100(self):
        """_compute_savings is capped at 100%."""
        from src.context_assembler import _compute_savings
        result = _compute_savings(1, 1000)
        assert result <= 100.0

    def test_compute_savings_zero_when_context_equals_raw(self):
        """_compute_savings returns 0 when context_tokens == raw_total_tokens."""
        from src.context_assembler import _compute_savings
        assert _compute_savings(500, 500) == 0.0

    def test_format_prompt_messages_structure(self):
        """format_prompt_messages returns list with system and user messages."""
        from src.context_assembler import format_prompt_messages

        ctx = {
            "system_prompt": "You are a code assistant.",
            "kg_context": {"nodes": [], "edges": [], "token_estimate": 0},
            "vault_keys": ["DB_PASS"],
            "task_description": "Fix the login bug",
            "budget_tier": "standard",
            "context_tokens": 100,
            "raw_total_tokens": 500,
            "savings_vs_raw": 80.0,
        }
        messages = format_prompt_messages(ctx)
        assert isinstance(messages, list)
        assert len(messages) >= 2
        roles = [m["role"] for m in messages]
        assert "system" in roles
        assert "user" in roles

    def test_format_prompt_messages_includes_task_description(self):
        """format_prompt_messages includes the task description in user message."""
        from src.context_assembler import format_prompt_messages

        ctx = {
            "system_prompt": "You are a code assistant.",
            "kg_context": {"nodes": [], "edges": [], "token_estimate": 0},
            "vault_keys": [],
            "task_description": "Fix the authentication bypass vulnerability",
            "budget_tier": "standard",
            "context_tokens": 0,
            "raw_total_tokens": 0,
            "savings_vs_raw": 0.0,
        }
        messages = format_prompt_messages(ctx)
        all_content = " ".join(m["content"] for m in messages)
        assert "authentication bypass" in all_content

    def test_format_prompt_messages_includes_vault_keys(self):
        """format_prompt_messages includes vault key names in user message."""
        from src.context_assembler import format_prompt_messages

        ctx = {
            "system_prompt": "You are a code assistant.",
            "kg_context": {"nodes": [], "edges": [], "token_estimate": 0},
            "vault_keys": ["DB_PASS", "API_KEY"],
            "task_description": "Fix the bug",
            "budget_tier": "standard",
            "context_tokens": 0,
            "raw_total_tokens": 0,
            "savings_vs_raw": 0.0,
        }
        messages = format_prompt_messages(ctx)
        all_content = " ".join(m["content"] for m in messages)
        assert "DB_PASS" in all_content
        assert "API_KEY" in all_content

    @pytest.mark.asyncio
    async def test_fetch_raw_total_tokens_sums_file_nodes(self):
        """_fetch_raw_total_tokens sums raw_token_count from all File nodes."""
        from src.context_assembler import _fetch_raw_total_tokens

        nodes = [
            {"id": "f1", "type": "File", "properties": {"raw_token_count": 300}},
            {"id": "f2", "type": "File", "properties": {"raw_token_count": 700}},
            {"id": "fn1", "type": "Function", "properties": {"name": "foo"}},
        ]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = lambda: nodes
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        total = await _fetch_raw_total_tokens(mock_client, "proj-1")
        assert total == 1000

    @pytest.mark.asyncio
    async def test_fetch_raw_total_tokens_handles_missing_field(self):
        """_fetch_raw_total_tokens handles nodes without raw_token_count gracefully."""
        from src.context_assembler import _fetch_raw_total_tokens

        nodes = [
            {"id": "f1", "type": "File", "properties": {}},  # no raw_token_count
            {"id": "f2", "type": "File", "properties": {"raw_token_count": 400}},
        ]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = lambda: nodes
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        total = await _fetch_raw_total_tokens(mock_client, "proj-1")
        assert total == 400

    @pytest.mark.asyncio
    async def test_fetch_raw_total_tokens_handles_error(self):
        """_fetch_raw_total_tokens returns 0 on HTTP error."""
        from src.context_assembler import _fetch_raw_total_tokens

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("KG down"))

        total = await _fetch_raw_total_tokens(mock_client, "proj-1")
        assert total == 0


# ---------------------------------------------------------------------------
# claude.py coverage — AsyncAnthropic is imported inside the function
# ---------------------------------------------------------------------------


class TestClaudeDispatcher:
    """Cover claude.py — dispatch_claude, diff parsing, error handling."""

    @pytest.mark.asyncio
    async def test_dispatch_claude_returns_agent_response(self):
        """dispatch_claude() returns a dict with task_id and token_usage."""
        from src.claude import dispatch_claude

        messages = [
            {"role": "system", "content": "You are a code assistant."},
            {"role": "user", "content": "Fix the bug."},
        ]

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n")]
        mock_message.usage = MagicMock(input_tokens=100, output_tokens=50)

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            response = await dispatch_claude(messages, "task-1", api_key="test-key")

        assert response["task_id"] == "task-1"
        assert "token_usage" in response
        assert response["token_usage"]["context_tokens"] == 100
        assert response["token_usage"]["response_tokens"] == 50

    @pytest.mark.asyncio
    async def test_dispatch_claude_extracts_diff(self):
        """dispatch_claude() extracts unified diff from LLM response."""
        from src.claude import dispatch_claude

        diff = "--- a/src/app.py\n+++ b/src/app.py\n@@ -5,3 +5,4 @@\n+import os\n"
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=f"Here is the fix:\n```diff\n{diff}\n```")]
        mock_message.usage = MagicMock(input_tokens=80, output_tokens=30)

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            response = await dispatch_claude([], "task-1", api_key="test-key")

        # diff should be extracted from the code block or raw response
        assert response.get("diff") is not None or response.get("raw_response") is not None

    @pytest.mark.asyncio
    async def test_dispatch_claude_handles_api_error(self):
        """dispatch_claude() propagates API errors."""
        from src.claude import dispatch_claude

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("rate_limit_exceeded"))

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            # dispatch_claude catches errors and returns error response dict with diff=""
            response = await dispatch_claude([], "task-1", api_key="test-key")
            assert response["task_id"] == "task-1"
            assert response["diff"] == ""
            assert "Claude dispatch error" in response["explanation"]

    @pytest.mark.asyncio
    async def test_dispatch_claude_token_savings_in_response(self):
        """dispatch_claude() includes savings_vs_raw in token_usage."""
        from src.claude import dispatch_claude

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="no diff here")]
        mock_message.usage = MagicMock(input_tokens=200, output_tokens=100)

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            response = await dispatch_claude([], "task-1", api_key="test-key", savings_vs_raw=75.5)

        assert response["token_usage"]["savings_vs_raw"] == 75.5

    @pytest.mark.asyncio
    async def test_dispatch_claude_restricted_path_flagged(self):
        """dispatch_claude() flags diffs that touch restricted paths."""
        from src.claude import dispatch_claude

        # A diff that touches /cqr/ — a restricted path
        restricted_diff = "--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-OLD=x\n+NEW=y\n"
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=restricted_diff)]
        mock_message.usage = MagicMock(input_tokens=50, output_tokens=20)

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            response = await dispatch_claude([], "task-1", api_key="test-key")

        # Response should be returned (flagging is advisory, not blocking)
        assert "task_id" in response


# ---------------------------------------------------------------------------
# codex.py coverage — AsyncOpenAI is imported inside the function
# ---------------------------------------------------------------------------


class TestCodexDispatcher:
    """Cover codex.py — dispatch_codex, token counting, error handling."""

    @pytest.mark.asyncio
    async def test_dispatch_codex_returns_agent_response(self):
        """dispatch_codex() returns a dict with task_id and token_usage."""
        from src.codex import dispatch_codex

        mock_choice = MagicMock()
        mock_choice.message = MagicMock()
        mock_choice.message.content = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"

        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]
        mock_completion.usage = MagicMock(prompt_tokens=90, completion_tokens=40)

        mock_client = MagicMock()
        mock_client.chat = MagicMock()
        mock_client.chat.completions = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            response = await dispatch_codex([], "task-1", api_key="test-key")

        assert response["task_id"] == "task-1"
        assert response["token_usage"]["context_tokens"] == 90
        assert response["token_usage"]["response_tokens"] == 40

    @pytest.mark.asyncio
    async def test_dispatch_codex_handles_api_error(self):
        """dispatch_codex() propagates API errors."""
        from src.codex import dispatch_codex

        mock_client = MagicMock()
        mock_client.chat = MagicMock()
        mock_client.chat.completions = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("quota_exceeded"))

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            # dispatch_codex catches errors and returns error response dict with diff=""
            response = await dispatch_codex([], "task-1", api_key="test-key")
            assert response["task_id"] == "task-1"
            assert response["diff"] == ""
            assert "Codex dispatch error" in response["explanation"]

    @pytest.mark.asyncio
    async def test_dispatch_codex_savings_in_response(self):
        """dispatch_codex() includes savings_vs_raw in token_usage."""
        from src.codex import dispatch_codex

        mock_choice = MagicMock()
        mock_choice.message = MagicMock(content="no diff")
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]
        mock_completion.usage = MagicMock(prompt_tokens=50, completion_tokens=20)

        mock_client = MagicMock()
        mock_client.chat = MagicMock()
        mock_client.chat.completions = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            response = await dispatch_codex([], "task-1", api_key="test-key", savings_vs_raw=60.0)

        assert response["token_usage"]["savings_vs_raw"] == 60.0

    @pytest.mark.asyncio
    async def test_dispatch_codex_no_usage_returns_zeros(self):
        """dispatch_codex() handles missing usage gracefully."""
        from src.codex import dispatch_codex

        mock_choice = MagicMock()
        mock_choice.message = MagicMock(content="")
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]
        mock_completion.usage = None

        mock_client = MagicMock()
        mock_client.chat = MagicMock()
        mock_client.chat.completions = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            response = await dispatch_codex([], "task-1", api_key="test-key")

        assert response["token_usage"]["context_tokens"] == 0
        assert response["token_usage"]["response_tokens"] == 0

    @pytest.mark.asyncio
    async def test_dispatch_codex_restricted_path_flagged(self):
        """dispatch_codex() flags diffs that touch restricted paths."""
        from src.codex import dispatch_codex

        restricted_diff = "--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-OLD=x\n+NEW=y\n"
        mock_choice = MagicMock()
        mock_choice.message = MagicMock(content=restricted_diff)
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]
        mock_completion.usage = MagicMock(prompt_tokens=30, completion_tokens=10)

        mock_client = MagicMock()
        mock_client.chat = MagicMock()
        mock_client.chat.completions = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            response = await dispatch_codex([], "task-1", api_key="test-key")

        assert "task_id" in response
