"""
Docker container lifecycle management for CQR execution environments.
Each project gets an isolated Ubuntu 22.04 container with a persistent /workspace volume.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import docker
import docker.errors
from docker.models.containers import Container

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DOCKER_IMAGE = os.getenv("CQR_CONTAINER_IMAGE", "cqr-sandbox:latest")
_MEMORY_LIMIT = os.getenv("CQR_MEMORY_LIMIT", "2g")
_CPU_PERIOD = int(os.getenv("CQR_CPU_PERIOD", "100000"))
_CPU_QUOTA = int(os.getenv("CQR_CPU_QUOTA", "200000"))  # 2 vCPU soft limit
_WORKSPACE_BASE = os.getenv("CQR_WORKSPACE_BASE", "/tmp/cqr-workspaces")
_IDLE_TIMEOUT_SECONDS = int(os.getenv("CQR_IDLE_TIMEOUT", "1800"))  # 30 min


def _client() -> docker.DockerClient:
    """Return a Docker SDK client."""
    return docker.from_env()


def _volume_name(project_id: str) -> str:
    """Return the Docker volume name for a project's persistent workspace."""
    return f"cqr-workspace-{project_id}"


def _container_name(project_id: str) -> str:
    """Return the Docker container name for a project."""
    return f"cqr-container-{project_id}"


# ---------------------------------------------------------------------------
# Container operations
# ---------------------------------------------------------------------------


def create_container(project_id: str, repo_path: str) -> dict[str, Any]:
    """
    Provision a new isolated container for a project.
    Creates a named Docker volume for workspace persistence.
    """
    client = _client()
    volume_name = _volume_name(project_id)
    container_name = _container_name(project_id)

    # Ensure volume exists
    try:
        client.volumes.get(volume_name)
        logger.info("Reusing existing volume %s", volume_name)
    except docker.errors.NotFound:
        client.volumes.create(name=volume_name)
        logger.info("Created volume %s", volume_name)

    # Remove stale container if present
    try:
        old = client.containers.get(container_name)
        old.remove(force=True)
        logger.info("Removed stale container %s", container_name)
    except docker.errors.NotFound:
        pass

    container: Container = client.containers.create(
        image=_DOCKER_IMAGE,
        name=container_name,
        detach=True,
        tty=True,
        mem_limit=_MEMORY_LIMIT,
        cpu_period=_CPU_PERIOD,
        cpu_quota=_CPU_QUOTA,
        network_mode="bridge",
        volumes={
            volume_name: {"bind": "/workspace", "mode": "rw"},
        },
        environment={
            "CQR_PROJECT_ID": project_id,
        },
        labels={"cqr.project_id": project_id},
    )
    logger.info(
        '{"event": "container_created", "project_id": "%s", "container_id": "%s"}',
        project_id,
        container.id,
    )
    return {"container_id": container.id, "container_name": container_name}


def start_container(container_id: str) -> dict[str, str]:
    """Start a stopped container."""
    client = _client()
    container = client.containers.get(container_id)
    container.start()
    logger.info('{"event": "container_started", "container_id": "%s"}', container_id)
    return {"status": "started", "container_id": container_id}


def stop_container(container_id: str) -> dict[str, str]:
    """Gracefully stop a container (workspace volume persists)."""
    client = _client()
    container = client.containers.get(container_id)
    container.stop(timeout=10)
    logger.info('{"event": "container_stopped", "container_id": "%s"}', container_id)
    return {"status": "stopped", "container_id": container_id}


def get_container_status(container_id: str) -> dict[str, str]:
    """Return the current status of a container."""
    client = _client()
    try:
        container = client.containers.get(container_id)
        return {"container_id": container_id, "status": container.status}
    except docker.errors.NotFound:
        return {"container_id": container_id, "status": "not_found"}
