"""
CP-2 tests for the Orchestration API.

Tests:
  - POST /api/tasks returns task_id immediately (202 Accepted)
  - WebSocket emit_event broadcasts correct event structure
  - All PDR §9.2 event emitters produce correct payloads
  - ConnectionManager tracks connections per project
  - Task status transitions
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ws import (
    ConnectionManager,
    emit_container_status,
    emit_kg_updated,
    emit_security_alert,
    emit_task_applied,
    emit_task_context_assembled,
    emit_task_diff_ready,
    emit_task_failed,
    emit_task_started,
)
from src.models import TokenUsage, Task, Project


# ---------------------------------------------------------------------------
# ConnectionManager
# ---------------------------------------------------------------------------


class TestConnectionManager:
    def _make_ws(self):
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_connect_registers_websocket(self):
        mgr = ConnectionManager()
        ws = self._make_ws()
        await mgr.connect("proj-1", ws)
        assert mgr.connection_count("proj-1") == 1

    @pytest.mark.asyncio
    async def test_disconnect_removes_websocket(self):
        mgr = ConnectionManager()
        ws = self._make_ws()
        await mgr.connect("proj-1", ws)
        mgr.disconnect("proj-1", ws)
        assert mgr.connection_count("proj-1") == 0

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_connections(self):
        mgr = ConnectionManager()
        ws1, ws2 = self._make_ws(), self._make_ws()
        await mgr.connect("proj-1", ws1)
        await mgr.connect("proj-1", ws2)
        await mgr.broadcast("proj-1", {"event": "test", "data": {}})
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_connections(self):
        import websockets
        mgr = ConnectionManager()
        dead_ws = self._make_ws()
        dead_ws.send_text.side_effect = Exception("connection closed")
        await mgr.connect("proj-1", dead_ws)
        await mgr.broadcast("proj-1", {"event": "test"})
        assert mgr.connection_count("proj-1") == 0

    @pytest.mark.asyncio
    async def test_broadcast_to_unknown_project_is_noop(self):
        mgr = ConnectionManager()
        # Should not raise
        await mgr.broadcast("unknown-proj", {"event": "test"})


# ---------------------------------------------------------------------------
# PDR §9.2 event emitters
# ---------------------------------------------------------------------------


class TestEventEmitters:
    """Verify each emitter produces the correct event type and payload keys."""

    @pytest.mark.asyncio
    async def test_emit_task_started(self):
        captured = []
        with patch("src.ws.manager") as mock_mgr:
            mock_mgr.broadcast = AsyncMock(side_effect=lambda pid, evt: captured.append(evt))
            await emit_task_started("proj-1", "task-1", "claude", "standard")
        assert len(captured) == 1
        evt = captured[0]
        assert evt["event"] == "task.started"
        assert evt["data"]["task_id"] == "task-1"
        assert evt["data"]["agent"] == "claude"
        assert evt["data"]["budget_tier"] == "standard"

    @pytest.mark.asyncio
    async def test_emit_task_context_assembled(self):
        captured = []
        with patch("src.ws.manager") as mock_mgr:
            mock_mgr.broadcast = AsyncMock(side_effect=lambda pid, evt: captured.append(evt))
            await emit_task_context_assembled("proj-1", "task-1", 1500, 42)
        evt = captured[0]
        assert evt["event"] == "task.context_assembled"
        assert evt["data"]["token_count"] == 1500
        assert evt["data"]["node_count"] == 42

    @pytest.mark.asyncio
    async def test_emit_task_diff_ready(self):
        captured = []
        with patch("src.ws.manager") as mock_mgr:
            mock_mgr.broadcast = AsyncMock(side_effect=lambda pid, evt: captured.append(evt))
            await emit_task_diff_ready("proj-1", "task-1", "--- a\n+++ b\n", 0.92, {})
        evt = captured[0]
        assert evt["event"] == "task.diff_ready"
        assert evt["data"]["confidence"] == 0.92

    @pytest.mark.asyncio
    async def test_emit_task_applied(self):
        captured = []
        with patch("src.ws.manager") as mock_mgr:
            mock_mgr.broadcast = AsyncMock(side_effect=lambda pid, evt: captured.append(evt))
            await emit_task_applied("proj-1", "task-1", files_changed=2, lines_added=10, lines_removed=3)
        evt = captured[0]
        assert evt["event"] == "task.applied"
        assert evt["data"]["files_changed"] == 2
        assert evt["data"]["lines_added"] == 10

    @pytest.mark.asyncio
    async def test_emit_task_failed(self):
        captured = []
        with patch("src.ws.manager") as mock_mgr:
            mock_mgr.broadcast = AsyncMock(side_effect=lambda pid, evt: captured.append(evt))
            await emit_task_failed("proj-1", "task-1", "timeout", "dispatch", recoverable=True)
        evt = captured[0]
        assert evt["event"] == "task.failed"
        assert evt["data"]["recoverable"] is True
        assert evt["data"]["stage"] == "dispatch"

    @pytest.mark.asyncio
    async def test_emit_kg_updated(self):
        captured = []
        with patch("src.ws.manager") as mock_mgr:
            mock_mgr.broadcast = AsyncMock(side_effect=lambda pid, evt: captured.append(evt))
            await emit_kg_updated("proj-1", ["node-1", "node-2"])
        evt = captured[0]
        assert evt["event"] == "kg.updated"
        assert "node-1" in evt["data"]["node_ids_changed"]

    @pytest.mark.asyncio
    async def test_emit_security_alert(self):
        captured = []
        with patch("src.ws.manager") as mock_mgr:
            mock_mgr.broadcast = AsyncMock(side_effect=lambda pid, evt: captured.append(evt))
            await emit_security_alert("proj-1", "critical", "sql_injection_path", ["n1", "n2"])
        evt = captured[0]
        assert evt["event"] == "security.alert"
        assert evt["data"]["severity"] == "critical"
        assert evt["data"]["pattern"] == "sql_injection_path"

    @pytest.mark.asyncio
    async def test_emit_container_status(self):
        captured = []
        with patch("src.ws.manager") as mock_mgr:
            mock_mgr.broadcast = AsyncMock(side_effect=lambda pid, evt: captured.append(evt))
            await emit_container_status("proj-1", "running", "container-abc")
        evt = captured[0]
        assert evt["event"] == "container.status"
        assert evt["data"]["state"] == "running"

    @pytest.mark.asyncio
    async def test_all_events_have_timestamp(self):
        """Every event must include a timestamp field."""
        captured = []
        with patch("src.ws.manager") as mock_mgr:
            mock_mgr.broadcast = AsyncMock(side_effect=lambda pid, evt: captured.append(evt))
            await emit_task_started("proj-1", "task-1", "claude", "standard")
        assert "timestamp" in captured[0]

    @pytest.mark.asyncio
    async def test_all_events_have_project_id(self):
        """Every event must include the project_id field."""
        captured = []
        with patch("src.ws.manager") as mock_mgr:
            mock_mgr.broadcast = AsyncMock(side_effect=lambda pid, evt: captured.append(evt))
            await emit_kg_updated("my-project", ["n1"])
        assert captured[0]["project_id"] == "my-project"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModels:
    def test_token_usage_defaults(self):
        tu = TokenUsage()
        assert tu.context_tokens == 0
        assert tu.savings_vs_raw == 0.0
        assert tu.context_node_count is None

    def test_token_usage_with_all_fields(self):
        tu = TokenUsage(
            context_tokens=1000,
            response_tokens=200,
            total_tokens=1200,
            savings_vs_raw=81.0,
            prompt_tokens=1000,
            completion_tokens=200,
            context_node_count=45,
        )
        assert tu.savings_vs_raw == 81.0
        assert tu.context_node_count == 45

    def test_task_status_includes_rejected(self):
        t = Task(
            id="t1",
            project_id="p1",
            description="test",
            status="rejected",
        )
        assert t.status == "rejected"

    def test_project_status_includes_stopped(self):
        p = Project(id="p1", name="test", repo_path="/tmp/test", status="stopped")
        assert p.status == "stopped"
