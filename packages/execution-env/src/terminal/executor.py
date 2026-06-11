"""
Shell command execution inside project containers.

All commands pass through a sanitizer before execution.
Every command is logged with project_id, session_id, and timestamp.

Supports both blocking exec (run_command) and streaming exec (stream_command).
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator

import docker
import docker.errors

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command sanitizer
# ---------------------------------------------------------------------------

_DENY_PATTERNS: list[re.Pattern] = [
    re.compile(r"rm\s+-rf\s+/(?!\w)"),       # rm -rf / (but not /workspace)
    re.compile(r"\bshutdown\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bhalt\b"),
    re.compile(r"\bpoweroff\b"),
    re.compile(r">\s*/cqr/"),
    re.compile(r"cat\s+/cqr/"),
    re.compile(r"cp\s+.*\s+/cqr/"),
    re.compile(r"mv\s+.*\s+/cqr/"),
    re.compile(r"chmod\s+.*\s+/cqr/"),
    re.compile(r"chown\s+.*\s+/cqr/"),
    re.compile(r"dd\s+.*of=/dev/"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bfdisk\b"),
    re.compile(r"\bnsenter\b"),
    re.compile(r"\bchroot\b"),
]


def sanitize_command(command: str, session_id: str) -> tuple[bool, str]:
    """
    Validate a shell command against the deny list.
    Returns (is_safe, reason). If is_safe is False, reason explains the rejection.
    """
    for pattern in _DENY_PATTERNS:
        if pattern.search(command):
            reason = f"Command denied by security policy (pattern: {pattern.pattern})"
            logger.warning(
                '{"event": "command_denied", "session_id": "%s", "command": "%s", "reason": "%s"}',
                session_id,
                command[:200],
                reason,
            )
            return False, reason
    return True, ""


# ---------------------------------------------------------------------------
# Blocking execution
# ---------------------------------------------------------------------------


def run_command(
    container_id: str,
    command: str,
    project_id: str,
    session_id: str | None = None,
    workdir: str = "/workspace",
) -> dict[str, Any]:
    """
    Execute a shell command in a container and return stdout/stderr.
    Command is sanitized before execution.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    is_safe, reason = sanitize_command(command, session_id)
    if not is_safe:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": reason,
            "rejected": True,
            "session_id": session_id,
        }

    logger.info(
        '{"event": "command_run", "project_id": "%s", "session_id": "%s", '
        '"command": "%s", "timestamp": "%s"}',
        project_id,
        session_id,
        command[:200],
        datetime.utcnow().isoformat(),
    )

    client = docker.from_env()
    try:
        container = client.containers.get(container_id)
        exit_code, output = container.exec_run(
            cmd=["bash", "-c", command],
            workdir=workdir,
            demux=True,
        )
        stdout_bytes, stderr_bytes = output if output else (b"", b"")
        return {
            "exit_code": exit_code,
            "stdout": (stdout_bytes or b"").decode("utf-8", errors="replace"),
            "stderr": (stderr_bytes or b"").decode("utf-8", errors="replace"),
            "rejected": False,
            "session_id": session_id,
        }
    except docker.errors.NotFound:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Container {container_id} not found",
            "rejected": False,
            "session_id": session_id,
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Command execution error: %s", exc)
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": str(exc),
            "rejected": False,
            "session_id": session_id,
        }


# ---------------------------------------------------------------------------
# Streaming execution (yields chunks as they arrive)
# ---------------------------------------------------------------------------


async def stream_command(
    container_id: str,
    command: str,
    project_id: str,
    session_id: str | None = None,
    workdir: str = "/workspace",
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Execute a shell command in a container and yield stdout/stderr chunks
    as they arrive. Each yielded dict has keys: type ('stdout'|'stderr'|'exit'),
    data (str), session_id.

    Usage:
        async for chunk in stream_command(...):
            await websocket.send_json(chunk)
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    is_safe, reason = sanitize_command(command, session_id)
    if not is_safe:
        yield {"type": "stderr", "data": reason, "session_id": session_id, "rejected": True}
        yield {"type": "exit", "data": "1", "session_id": session_id, "rejected": True}
        return

    logger.info(
        '{"event": "command_stream", "project_id": "%s", "session_id": "%s", "command": "%s"}',
        project_id,
        session_id,
        command[:200],
    )

    import asyncio

    client = docker.from_env()
    try:
        container = client.containers.get(container_id)
        # exec_id with stream=True gives us a generator of raw bytes
        exec_id = client.api.exec_create(
            container.id,
            cmd=["bash", "-c", command],
            workdir=workdir,
            stdout=True,
            stderr=True,
        )
        output_gen = client.api.exec_start(exec_id["Id"], stream=True, demux=True)

        loop = asyncio.get_event_loop()

        def _read_chunks():
            for stdout_chunk, stderr_chunk in output_gen:
                if stdout_chunk:
                    yield {"type": "stdout", "data": stdout_chunk.decode("utf-8", errors="replace"), "session_id": session_id}
                if stderr_chunk:
                    yield {"type": "stderr", "data": stderr_chunk.decode("utf-8", errors="replace"), "session_id": session_id}

        # Run the blocking generator in a thread pool to avoid blocking the event loop
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            for chunk in await loop.run_in_executor(pool, lambda: list(_read_chunks())):
                yield chunk

        # Get exit code
        inspect = client.api.exec_inspect(exec_id["Id"])
        exit_code = inspect.get("ExitCode", 0)
        yield {"type": "exit", "data": str(exit_code), "session_id": session_id}

    except docker.errors.NotFound:
        yield {"type": "stderr", "data": f"Container {container_id} not found", "session_id": session_id}
        yield {"type": "exit", "data": "1", "session_id": session_id}
    except Exception as exc:  # noqa: BLE001
        logger.error("Stream command error: %s", exc)
        yield {"type": "stderr", "data": str(exc), "session_id": session_id}
        yield {"type": "exit", "data": "1", "session_id": session_id}
