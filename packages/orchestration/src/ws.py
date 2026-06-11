"""
WebSocket event streaming layer for the CQR Orchestration API.

Frontend connects to ws://host/ws/{project_id} and receives a stream of typed events.
All event types are defined in PDR §9.2.

Event taxonomy:
  task.started          — agent_id, budget_tier
  task.context_assembled — token_count, node_count
  task.streaming        — delta (text chunk from LLM)
  task.diff_ready       — diff, confidence, token_usage
  task.applied          — files_changed, lines_added, lines_removed
  task.failed           — error, stage, recoverable
  kg.updated            — node_ids_changed
  security.alert        — severity, path, node_ids
  container.status      — state (running/stopped/error)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
ws_router = APIRouter()

# ---------------------------------------------------------------------------
# Connection manager — tracks active WebSocket connections per project
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Manages active WebSocket connections grouped by project ID."""

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, project_id: str, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self._connections.setdefault(project_id, []).append(websocket)
        logger.info('{"event": "ws_connected", "project_id": "%s"}', project_id)

    def disconnect(self, project_id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the registry."""
        connections = self._connections.get(project_id, [])
        if websocket in connections:
            connections.remove(websocket)
        logger.info('{"event": "ws_disconnected", "project_id": "%s"}', project_id)

    async def broadcast(self, project_id: str, event: dict[str, Any]) -> None:
        """Broadcast a typed event to all connections for a project."""
        connections = list(self._connections.get(project_id, []))
        dead: list[WebSocket] = []
        for ws in connections:
            try:
                await ws.send_text(json.dumps(event, default=str))
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(project_id, ws)

    def connection_count(self, project_id: str) -> int:
        """Return the number of active connections for a project."""
        return len(self._connections.get(project_id, []))


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@ws_router.websocket("/ws/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: str) -> None:
    """
    WebSocket endpoint for real-time event streaming per project.

    PDR §9.2 event types emitted:
      task.started, task.context_assembled, task.streaming, task.diff_ready,
      task.applied, task.failed, kg.updated, security.alert, container.status
    """
    await manager.connect(project_id, websocket)
    try:
        # Send initial connection acknowledgement
        await websocket.send_text(
            json.dumps({
                "event": "connected",
                "project_id": project_id,
                "timestamp": datetime.utcnow().isoformat(),
                "data": {
                    "message": "CQR event stream connected",
                    "supported_events": [
                        "task.started",
                        "task.context_assembled",
                        "task.streaming",
                        "task.diff_ready",
                        "task.applied",
                        "task.failed",
                        "kg.updated",
                        "security.alert",
                        "container.status",
                    ],
                },
            })
        )

        # Keep connection alive — relay pings and send heartbeats
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                msg = json.loads(data) if data.startswith("{") else {"type": data}
                if msg.get("type") == "ping" or data == "ping":
                    await websocket.send_text(
                        json.dumps({
                            "event": "pong",
                            "project_id": project_id,
                            "timestamp": datetime.utcnow().isoformat(),
                        })
                    )
            except asyncio.TimeoutError:
                # Send heartbeat to detect stale connections
                await websocket.send_text(
                    json.dumps({
                        "event": "heartbeat",
                        "project_id": project_id,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                )
            except json.JSONDecodeError:
                pass  # ignore malformed messages

    except WebSocketDisconnect:
        manager.disconnect(project_id, websocket)


# ---------------------------------------------------------------------------
# Internal broadcast helpers (called by router background tasks)
# ---------------------------------------------------------------------------


async def emit_event(project_id: str, event_type: str, data: dict[str, Any]) -> None:
    """
    Emit a typed event to all WebSocket subscribers for a project.
    This is the primary integration point — called from router.py background tasks.
    """
    event = {
        "event": event_type,
        "project_id": project_id,
        "timestamp": datetime.utcnow().isoformat(),
        "data": data,
    }
    await manager.broadcast(project_id, event)


# ---------------------------------------------------------------------------
# Convenience emitters for each PDR §9.2 event type
# ---------------------------------------------------------------------------


async def emit_task_started(project_id: str, task_id: str, agent: str, budget_tier: str) -> None:
    await emit_event(project_id, "task.started", {
        "task_id": task_id,
        "agent": agent,
        "budget_tier": budget_tier,
    })


async def emit_task_context_assembled(
    project_id: str, task_id: str, token_count: int, node_count: int
) -> None:
    await emit_event(project_id, "task.context_assembled", {
        "task_id": task_id,
        "token_count": token_count,
        "node_count": node_count,
    })


async def emit_task_streaming(project_id: str, task_id: str, delta: str) -> None:
    await emit_event(project_id, "task.streaming", {
        "task_id": task_id,
        "delta": delta,
    })


async def emit_task_diff_ready(
    project_id: str,
    task_id: str,
    diff: str,
    confidence: float,
    token_usage: dict,
) -> None:
    await emit_event(project_id, "task.diff_ready", {
        "task_id": task_id,
        "diff": diff,
        "confidence": confidence,
        "token_usage": token_usage,
    })


async def emit_task_applied(
    project_id: str,
    task_id: str,
    files_changed: int,
    lines_added: int,
    lines_removed: int,
) -> None:
    await emit_event(project_id, "task.applied", {
        "task_id": task_id,
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
    })


async def emit_task_failed(
    project_id: str, task_id: str, error: str, stage: str, recoverable: bool = False
) -> None:
    await emit_event(project_id, "task.failed", {
        "task_id": task_id,
        "error": error,
        "stage": stage,
        "recoverable": recoverable,
    })


async def emit_kg_updated(project_id: str, node_ids_changed: list[str]) -> None:
    await emit_event(project_id, "kg.updated", {
        "node_ids_changed": node_ids_changed,
    })


async def emit_security_alert(
    project_id: str,
    severity: str,
    pattern: str,
    node_ids: list[str],
    task_id: str | None = None,
) -> None:
    await emit_event(project_id, "security.alert", {
        "severity": severity,
        "pattern": pattern,
        "node_ids": node_ids,
        "task_id": task_id,
    })


async def emit_container_status(project_id: str, state: str, container_id: str) -> None:
    await emit_event(project_id, "container.status", {
        "state": state,
        "container_id": container_id,
    })
