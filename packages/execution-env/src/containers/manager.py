"""
Docker container lifecycle management for CQR execution environments.

Each project gets an isolated Linux container with:
  - A named Docker volume mounted at /workspace (persistent)
  - The cqr-sandbox image with the full PDR §6.2 toolchain
  - Resource limits (memory, CPU)
  - A project-scoped git identity

Supported operations: create, start, stop, restart, remove, status, logs.
"""
from __future__ import annotations

import logging
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import docker
import docker.errors
from docker.models.containers import Container

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DOCKER_IMAGE = os.getenv("CQR_CONTAINER_IMAGE", "cqr-sandbox:latest")
_FALLBACK_IMAGE = "ubuntu:22.04"          # used when cqr-sandbox is not built yet
_MEMORY_LIMIT = os.getenv("CQR_MEMORY_LIMIT", "2g")
_CPU_PERIOD = int(os.getenv("CQR_CPU_PERIOD", "100000"))
_CPU_QUOTA = int(os.getenv("CQR_CPU_QUOTA", "200000"))   # 2 vCPU soft limit
_WORKSPACE_BASE = os.getenv("CQR_WORKSPACE_BASE", "/tmp/cqr-workspaces")
_IDLE_TIMEOUT_SECONDS = int(os.getenv("CQR_IDLE_TIMEOUT", "1800"))  # 30 min


def _client() -> docker.DockerClient:
    """Return a Docker SDK client (raises if Docker is not available)."""
    return docker.from_env()


def _volume_name(project_id: str) -> str:
    return f"cqr-workspace-{project_id}"


def _container_name(project_id: str) -> str:
    return f"cqr-container-{project_id}"


def _resolve_image(client: docker.DockerClient) -> str:
    """Return the best available sandbox image."""
    try:
        client.images.get(_DOCKER_IMAGE)
        return _DOCKER_IMAGE
    except docker.errors.ImageNotFound:
        logger.warning(
            "Image %s not found — falling back to %s. "
            "Run `docker build` in packages/execution-env/docker/ to build the full image.",
            _DOCKER_IMAGE,
            _FALLBACK_IMAGE,
        )
        return _FALLBACK_IMAGE


# ---------------------------------------------------------------------------
# Container operations
# ---------------------------------------------------------------------------


def create_container(project_id: str, repo_path: str) -> dict[str, Any]:
    """
    Provision a new isolated container for a project.

    Steps:
      1. Create (or reuse) a named Docker volume for /workspace persistence.
      2. Remove any stale container with the same name.
      3. Create the container from the sandbox image.
      4. If repo_path is a local directory, copy its contents into /workspace.

    Returns container_id and container_name.
    """
    client = _client()
    volume_name = _volume_name(project_id)
    container_name = _container_name(project_id)
    image = _resolve_image(client)

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
        image=image,
        name=container_name,
        detach=True,
        tty=True,
        stdin_open=True,
        mem_limit=_MEMORY_LIMIT,
        cpu_period=_CPU_PERIOD,
        cpu_quota=_CPU_QUOTA,
        network_mode="bridge",
        volumes={
            volume_name: {"bind": "/workspace", "mode": "rw"},
        },
        environment={
            "CQR_PROJECT_ID": project_id,
            "GIT_AUTHOR_NAME": "CQR Agent",
            "GIT_AUTHOR_EMAIL": "agent@cqr.dev",
            "GIT_COMMITTER_NAME": "CQR Agent",
            "GIT_COMMITTER_EMAIL": "agent@cqr.dev",
        },
        labels={"cqr.project_id": project_id},
        working_dir="/workspace",
    )

    logger.info(
        '{"event": "container_created", "project_id": "%s", "container_id": "%s", "image": "%s"}',
        project_id,
        container.id,
        image,
    )

    # Bootstrap: copy repo_path into /workspace if it is a local directory
    if repo_path and Path(repo_path).is_dir():
        _copy_repo_to_container(container, repo_path)

    return {"container_id": container.id, "container_name": container_name}


def _copy_repo_to_container(container: Container, repo_path: str) -> None:
    """
    Copy the contents of a local directory into /workspace inside the container.
    Uses the Docker SDK put_archive API (no shell required).
    """
    try:
        with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
            tmp_path = tmp.name

        with tarfile.open(tmp_path, "w") as tar:
            source = Path(repo_path)
            for item in source.rglob("*"):
                if item.is_file():
                    arcname = item.relative_to(source)
                    tar.add(str(item), arcname=str(arcname))

        with open(tmp_path, "rb") as f:
            container.put_archive("/workspace", f.read())

        logger.info(
            '{"event": "workspace_bootstrapped", "container_id": "%s", "source": "%s"}',
            container.id,
            repo_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to copy repo to container: %s", exc)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:  # noqa: BLE001
            pass


def start_container(container_id: str) -> dict[str, Any]:
    """Start a stopped container and initialize git if needed."""
    client = _client()
    container = client.containers.get(container_id)
    container.start()

    # Initialize git repo in /workspace if not already present
    try:
        exit_code, _ = container.exec_run(
            ["bash", "-c",
             "cd /workspace && git rev-parse --git-dir 2>/dev/null || "
             "(git init && git add -A && git commit -m 'initial' --allow-empty 2>/dev/null || true)"],
            demux=False,
        )
    except Exception:  # noqa: BLE001
        pass

    logger.info('{"event": "container_started", "container_id": "%s"}', container_id)
    return {"status": "started", "container_id": container_id}


def stop_container(container_id: str) -> dict[str, Any]:
    """Gracefully stop a container (workspace volume persists)."""
    client = _client()
    container = client.containers.get(container_id)
    container.stop(timeout=10)
    logger.info('{"event": "container_stopped", "container_id": "%s"}', container_id)
    return {"status": "stopped", "container_id": container_id}


def restart_container(container_id: str) -> dict[str, Any]:
    """Restart a container (stop + start in one operation)."""
    client = _client()
    container = client.containers.get(container_id)
    container.restart(timeout=10)
    logger.info('{"event": "container_restarted", "container_id": "%s"}', container_id)
    return {"status": "restarted", "container_id": container_id}


def remove_container(container_id: str, remove_volume: bool = False) -> dict[str, Any]:
    """
    Remove a container. Optionally remove its workspace volume.
    The volume is preserved by default to allow project recovery.
    """
    client = _client()
    try:
        container = client.containers.get(container_id)
        project_id = container.labels.get("cqr.project_id", "")
        container.remove(force=True)
        if remove_volume and project_id:
            try:
                vol = client.volumes.get(_volume_name(project_id))
                vol.remove()
                logger.info("Removed volume for project %s", project_id)
            except docker.errors.NotFound:
                pass
        logger.info('{"event": "container_removed", "container_id": "%s"}', container_id)
        return {"status": "removed", "container_id": container_id}
    except docker.errors.NotFound:
        return {"status": "not_found", "container_id": container_id}


def get_container_status(container_id: str) -> dict[str, Any]:
    """Return the current status and metadata of a container."""
    client = _client()
    try:
        container = client.containers.get(container_id)
        attrs = container.attrs or {}
        state = attrs.get("State", {})
        return {
            "container_id": container_id,
            "status": container.status,
            "running": state.get("Running", False),
            "started_at": state.get("StartedAt", ""),
            "finished_at": state.get("FinishedAt", ""),
            "exit_code": state.get("ExitCode", 0),
            "image": attrs.get("Config", {}).get("Image", ""),
            "name": container.name,
        }
    except docker.errors.NotFound:
        return {"container_id": container_id, "status": "not_found", "running": False}


def get_container_logs(container_id: str, tail: int = 100) -> dict[str, Any]:
    """Return the last N lines of container stdout/stderr logs."""
    client = _client()
    try:
        container = client.containers.get(container_id)
        raw = container.logs(tail=tail, stdout=True, stderr=True)
        return {
            "container_id": container_id,
            "logs": raw.decode("utf-8", errors="replace"),
        }
    except docker.errors.NotFound:
        return {"container_id": container_id, "logs": "", "error": "not_found"}


def get_container_by_project(project_id: str) -> dict[str, Any]:
    """Look up a container by project ID label."""
    client = _client()
    containers = client.containers.list(
        all=True,
        filters={"label": f"cqr.project_id={project_id}"},
    )
    if not containers:
        return {}
    c = containers[0]
    return get_container_status(c.id)
