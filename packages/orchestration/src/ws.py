"""
WebSocket event streaming layer for the CQR Orchestration API.
Frontend connects to ws://host/ws/{project_id} and receives typed events.
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
        logger.info(
            '{"event": "ws_connected", "project_id": "%s"}', project_id
        )

    def disconnect(self, project_id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the registry."""
        connections = self._connections.get(project_id, [])
        if websocket in connections:
            connections.remove(websocket)
        logger.info(
            '{"event": "ws_disconnected", "project_id": "%s"}', project_id
        )

    async def broadcast(self, project_id: str, event: dict[str, Any]) -> None:
        """Broadcast a typed event to all connections for a project."""
        connections = self._connections.get(project_id, [])
        dead: list[WebSocket] = []
        for ws in connections:
            try:
                await ws.send_text(json.dumps(event, default=str))
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(project_id, ws)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@ws_router.websocket("/ws/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: str) -> None:
    """
    WebSocket endpoint for real-time event streaming per project.

    Emitted event types:
      - task.queued       — task accepted and queued
      - task.running      — agent dispatch started
      - task.done         — diff applied, task complete
      - task.failed       — task encountered an error
      - kg.ingested       — KG re-ingestion complete
      - security.alert    — vulnerability detected
      - container.started — execution container started
      - container.stopped — execution container stopped
    """
    await manager.connect(project_id, websocket)
    try:
        # Send initial connection acknowledgement
        await websocket.send_text(
            json.dumps(
                {
                    "event": "connected",
                    "project_id": project_id,
                    "timestamp": datetime.utcnow().isoformat(),
                    "data": {"message": "CQR event stream connected"},
                }
            )
        )
        # Keep connection alive and relay any incoming pings
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_text(
                        json.dumps({"event": "pong", "project_id": project_id})
                    )
            except asyncio.TimeoutError:
                # Send heartbeat to detect stale connections
                await websocket.send_text(
                    json.dumps({"event": "heartbeat", "project_id": project_id})
                )
    except WebSocketDisconnect:
        manager.disconnect(project_id, websocket)


# ---------------------------------------------------------------------------
# Internal broadcast helper (called by router background tasks)
# ---------------------------------------------------------------------------


async def emit_event(project_id: str, event_type: str, data: dict[str, Any]) -> None:
    """Emit a typed event to all WebSocket subscribers for a project."""
    event = {
        "event": event_type,
        "project_id": project_id,
        "timestamp": datetime.utcnow().isoformat(),
        "data": data,
    }
    await manager.broadcast(project_id, event)
