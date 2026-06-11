"""
Shell command execution inside project containers.
All commands pass through a sanitizer before execution.
Every command is logged with project_id, session_id, and timestamp.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Generator

import docker
import docker.errors

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command sanitizer
# ---------------------------------------------------------------------------

# Deny list patterns — any command matching these is rejected
_DENY_PATTERNS: list[re.Pattern] = [
    re.compile(r"rm\s+-rf\s+/"),
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
    re.compile(r"mkfs"),
    re.compile(r"fdisk"),
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
# Command execution
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
        '{"event": "command_run", "project_id": "%s", "session_id": "%s", "command": "%s", "timestamp": "%s"}',
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
