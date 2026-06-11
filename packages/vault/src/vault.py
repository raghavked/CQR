"""
CQR .ENV Vault — encrypted secret storage.

Security model:
  - Master key stored in OS keychain (never on disk, never in env, never logged)
  - Per-secret encryption using Fernet (AES-128-CBC + HMAC-SHA256)
  - Agents receive key names only — never values
  - Real values injected at container start via inject-env.sh (privileged)
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any

import keyring
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_KEYCHAIN_SERVICE = "cqr-vault"
_MASTER_KEY_ACCOUNT = "master-key"


# ---------------------------------------------------------------------------
# Master key management
# ---------------------------------------------------------------------------


def _get_or_create_master_key() -> bytes:
    """
    Retrieve the master Fernet key from the OS keychain.
    If no key exists, generate and store a new one.
    Master key is NEVER written to disk or logged.
    """
    stored = keyring.get_password(_KEYCHAIN_SERVICE, _MASTER_KEY_ACCOUNT)
    if stored:
        return stored.encode("ascii")

    # Generate a new master key
    new_key = Fernet.generate_key()
    keyring.set_password(_KEYCHAIN_SERVICE, _MASTER_KEY_ACCOUNT, new_key.decode("ascii"))
    logger.info("Generated new vault master key and stored in OS keychain")
    return new_key


def _fernet() -> Fernet:
    """Return a Fernet instance initialised with the master key."""
    return Fernet(_get_or_create_master_key())


# ---------------------------------------------------------------------------
# Secret storage (in-memory fallback for environments without keyring backend)
# TODO(AMBIGUITY): In production, replace in-memory store with encrypted DB rows
# ---------------------------------------------------------------------------

_secret_store: dict[str, str] = {}  # key: "{project_id}:{key_name}", value: encrypted token


def _store_key(project_id: str, key_name: str) -> str:
    """Return the internal storage key for a project secret."""
    return f"{project_id}:{key_name}"


def store_secret(project_id: str, key_name: str, value: str) -> None:
    """
    Encrypt and store a secret for a project.
    The plaintext value is never logged.
    """
    f = _fernet()
    encrypted = f.encrypt(value.encode("utf-8")).decode("ascii")
    storage_key = _store_key(project_id, key_name)

    # Try OS keychain first; fall back to in-memory store
    try:
        keyring.set_password(_KEYCHAIN_SERVICE, storage_key, encrypted)
    except Exception:  # noqa: BLE001
        _secret_store[storage_key] = encrypted

    logger.info(
        '{"event": "secret_stored", "project_id": "%s", "key_name": "%s"}',
        project_id,
        key_name,
    )


def get_secret(project_id: str, key_name: str) -> str | None:
    """
    Decrypt and return a secret value.
    Returns None if the key does not exist.
    NEVER call this from agent context — use list_secret_keys() instead.
    """
    storage_key = _store_key(project_id, key_name)

    encrypted: str | None = None
    try:
        encrypted = keyring.get_password(_KEYCHAIN_SERVICE, storage_key)
    except Exception:  # noqa: BLE001
        pass
    if not encrypted:
        encrypted = _secret_store.get(storage_key)

    if not encrypted:
        return None

    try:
        f = _fernet()
        return f.decrypt(encrypted.encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error(
            '{"event": "secret_decrypt_failed", "project_id": "%s", "key_name": "%s"}',
            project_id,
            key_name,
        )
        return None


def list_secret_keys(project_id: str) -> list[str]:
    """
    Return key names only for a project — NO values.
    This is the only vault function safe to expose to agent context.
    """
    prefix = f"{project_id}:"
    keys: list[str] = []

    # Collect from in-memory store
    for k in _secret_store:
        if k.startswith(prefix):
            keys.append(k[len(prefix):])

    # TODO(AMBIGUITY): Enumerate keychain entries when keyring backend supports it
    return sorted(set(keys))


def delete_secret(project_id: str, key_name: str) -> bool:
    """Delete a single secret. Returns True if deleted, False if not found."""
    storage_key = _store_key(project_id, key_name)
    deleted = False

    try:
        keyring.delete_password(_KEYCHAIN_SERVICE, storage_key)
        deleted = True
    except Exception:  # noqa: BLE001
        pass

    if storage_key in _secret_store:
        del _secret_store[storage_key]
        deleted = True

    if deleted:
        logger.info(
            '{"event": "secret_deleted", "project_id": "%s", "key_name": "%s"}',
            project_id,
            key_name,
        )
    return deleted


def delete_project_secrets(project_id: str) -> int:
    """Wipe all secrets for a project. Returns the count of deleted secrets."""
    keys = list_secret_keys(project_id)
    count = 0
    for key_name in keys:
        if delete_secret(project_id, key_name):
            count += 1
    logger.info(
        '{"event": "project_secrets_wiped", "project_id": "%s", "count": %d}',
        project_id,
        count,
    )
    return count


def inject_to_env(project_id: str) -> dict[str, str]:
    """
    Return a dict of {key_name: plaintext_value} for all project secrets.
    CRITICAL: This function must only be called by the execution-env package
    at container start time. It must NEVER be exposed via the public API.
    """
    keys = list_secret_keys(project_id)
    env_dict: dict[str, str] = {}
    for key_name in keys:
        value = get_secret(project_id, key_name)
        if value is not None:
            env_dict[key_name] = value
    return env_dict
