"""
Vault internal FastAPI router.
CRITICAL: The /vault/inject endpoint is for internal IPC only.
It must NEVER be registered in the public Orchestration API.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

from .vault import (
    delete_project_secrets,
    delete_secret,
    inject_to_env,
    list_secret_keys,
    store_secret,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CQR Vault",
    description=".ENV secret storage — internal IPC only.",
    version="0.1.0",
)

# Internal IPC token — only execution-env knows this value
_INTERNAL_IPC_TOKEN = os.getenv("VAULT_IPC_TOKEN", "")


def _require_ipc_token(x_cqr_ipc_token: str | None = Header(default=None)) -> None:
    """Verify the internal IPC token for privileged vault operations."""
    if not _INTERNAL_IPC_TOKEN:
        # TODO(AMBIGUITY): In production, VAULT_IPC_TOKEN must always be set
        logger.warning("VAULT_IPC_TOKEN not configured — inject endpoint is unprotected")
        return
    if x_cqr_ipc_token != _INTERNAL_IPC_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid IPC token")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class StoreSecretRequest(BaseModel):
    """Request body for POST /vault/store."""

    project_id: str
    key_name: str
    value: str


class DeleteSecretRequest(BaseModel):
    """Request body for DELETE /vault/key."""

    project_id: str
    key_name: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Return service health status."""
    return {"status": "ok", "service": "vault"}


@app.post("/vault/store", tags=["vault"])
async def store(body: StoreSecretRequest) -> dict[str, str]:
    """Store an encrypted secret for a project."""
    store_secret(body.project_id, body.key_name, body.value)
    return {"status": "ok", "key_name": body.key_name}


@app.get("/vault/keys/{project_id}", tags=["vault"])
async def list_keys(project_id: str) -> dict[str, Any]:
    """List key names (no values) for a project."""
    keys = list_secret_keys(project_id)
    return {"project_id": project_id, "keys": keys}


@app.post("/vault/inject/{project_id}", tags=["vault"])
async def inject(
    project_id: str,
    x_cqr_ipc_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """
    Inject real secret values into a container env dict.
    CRITICAL: Only callable from execution-env via internal IPC.
    Never exposed through the public Orchestration API.
    """
    _require_ipc_token(x_cqr_ipc_token)
    env_dict = inject_to_env(project_id)
    # Return values only over internal IPC — never log them
    return {"project_id": project_id, "env": env_dict}


@app.delete("/vault/key", tags=["vault"])
async def delete_key(body: DeleteSecretRequest) -> dict[str, Any]:
    """Delete a single secret."""
    deleted = delete_secret(body.project_id, body.key_name)
    return {"status": "ok" if deleted else "not_found", "key_name": body.key_name}


@app.delete("/vault/project/{project_id}", tags=["vault"])
async def delete_project(project_id: str) -> dict[str, Any]:
    """Wipe all secrets for a project."""
    count = delete_project_secrets(project_id)
    return {"status": "ok", "deleted_count": count}
