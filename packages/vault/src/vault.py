"""
CQR .ENV Vault — encrypted secret storage.

Security model:
  - Master key stored in OS keychain (never on disk in plaintext, never in env, never logged)
  - Per-secret encryption using Fernet (AES-128-CBC + HMAC-SHA256)
  - Agents receive key names only — never values
  - Real values injected at container start via inject-env.sh (privileged)

Keyring backend selection:
  - Linux with SecretService (GNOME Keyring / KWallet): uses DBus backend
  - Headless Linux (CI, server): uses keyrings.alt.file.EncryptedKeyring
  - The file-based backend stores an AES-encrypted JSON file at
    ~/.local/share/python_keyring/keyring_pass.cfg
  - NO in-memory fallback — if keyring is unavailable, operations raise RuntimeError.

Key enumeration:
  - keyring does not provide a native list() API.
  - We maintain a separate index: keyring entry ("cqr-vault-index", "{project_id}")
    stores a JSON array of key names for that project.
  - The index is updated atomically on every store/delete.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import keyring
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_KEYCHAIN_SERVICE = "cqr-vault"
_KEYCHAIN_INDEX_SERVICE = "cqr-vault-index"
_MASTER_KEY_ACCOUNT = "master-key"


# ---------------------------------------------------------------------------
# Backend bootstrap — ensure a working keyring backend is configured
# ---------------------------------------------------------------------------


def _ensure_keyring_backend() -> None:
    """
    Ensure a working keyring backend is available.
    On headless Linux (no SecretService), configure keyrings.alt.file.EncryptedKeyring.
    Raises RuntimeError if no backend can be configured.
    """
    # Test if the default backend works
    try:
        test_key = "__cqr_backend_test__"
        keyring.set_password("cqr-test", test_key, "ok")
        val = keyring.get_password("cqr-test", test_key)
        keyring.delete_password("cqr-test", test_key)
        if val == "ok":
            return  # Default backend works
    except Exception:
        pass

    # Fall back to keyrings.alt.file.EncryptedKeyring
    try:
        from keyrings.alt.file import EncryptedKeyring  # type: ignore[import]
        kr = EncryptedKeyring()
        keyring.set_keyring(kr)
        logger.info("Using keyrings.alt.file.EncryptedKeyring (headless Linux mode)")
        return
    except Exception:
        pass

    # Last resort: PlaintextKeyring (development only)
    try:
        from keyrings.alt.file import PlaintextKeyring  # type: ignore[import]
        kr = PlaintextKeyring()
        keyring.set_keyring(kr)
        logger.warning(
            "Using PlaintextKeyring — NOT suitable for production. "
            "Set VAULT_MASTER_KEY env var to override."
        )
        return
    except Exception:
        pass

    raise RuntimeError(
        "No working keyring backend found. Install keyrings.alt or configure "
        "a SecretService-compatible keyring daemon."
    )


# Bootstrap on module import
_ensure_keyring_backend()


# ---------------------------------------------------------------------------
# Master key management
# ---------------------------------------------------------------------------


def _get_or_create_master_key() -> bytes:
    """
    Retrieve the master Fernet key from the OS keychain.
    If no key exists, generate and store a new one.
    Master key is NEVER written to disk in plaintext or logged.

    Environment override: VAULT_MASTER_KEY (base64url Fernet key) — for testing only.
    """
    # Allow test override via env var (never used in production)
    env_key = os.getenv("VAULT_MASTER_KEY")
    if env_key:
        return env_key.encode("ascii")

    stored = keyring.get_password(_KEYCHAIN_SERVICE, _MASTER_KEY_ACCOUNT)
    if stored:
        return stored.encode("ascii")

    # Generate a new master key and store it
    new_key = Fernet.generate_key()
    keyring.set_password(_KEYCHAIN_SERVICE, _MASTER_KEY_ACCOUNT, new_key.decode("ascii"))
    logger.info("Generated new vault master key and stored in OS keychain")
    return new_key


def _fernet() -> Fernet:
    """Return a Fernet instance initialised with the master key."""
    return Fernet(_get_or_create_master_key())


# ---------------------------------------------------------------------------
# Key index management (enables list_secret_keys without native keyring list())
# ---------------------------------------------------------------------------


def _get_index(project_id: str) -> list[str]:
    """Return the list of key names stored for a project."""
    raw = keyring.get_password(_KEYCHAIN_INDEX_SERVICE, project_id)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _set_index(project_id: str, keys: list[str]) -> None:
    """Persist the key name index for a project."""
    keyring.set_password(_KEYCHAIN_INDEX_SERVICE, project_id, json.dumps(sorted(set(keys))))


def _add_to_index(project_id: str, key_name: str) -> None:
    """Add a key name to the project index."""
    keys = _get_index(project_id)
    if key_name not in keys:
        keys.append(key_name)
        _set_index(project_id, keys)


def _remove_from_index(project_id: str, key_name: str) -> None:
    """Remove a key name from the project index."""
    keys = _get_index(project_id)
    if key_name in keys:
        keys.remove(key_name)
        _set_index(project_id, keys)


# ---------------------------------------------------------------------------
# Secret storage — keyring only, no in-memory fallback
# ---------------------------------------------------------------------------


def _storage_account(project_id: str, key_name: str) -> str:
    """Return the keyring account name for a project secret."""
    return f"{project_id}:{key_name}"


def store_secret(project_id: str, key_name: str, value: str) -> None:
    """
    Encrypt and store a secret for a project in the OS keychain.
    The plaintext value is never logged.
    Raises RuntimeError if the keyring backend is unavailable.
    """
    f = _fernet()
    encrypted = f.encrypt(value.encode("utf-8")).decode("ascii")
    account = _storage_account(project_id, key_name)

    # Store in keyring — no in-memory fallback
    keyring.set_password(_KEYCHAIN_SERVICE, account, encrypted)

    # Update the key index
    _add_to_index(project_id, key_name)

    logger.info(
        '{"event": "secret_stored", "project_id": "%s", "key_name": "%s"}',
        project_id,
        key_name,
    )


def get_secret(project_id: str, key_name: str) -> str | None:
    """
    Decrypt and return a secret value from the keychain.
    Returns None if the key does not exist.
    NEVER call this from agent context — use list_secret_keys() instead.
    """
    account = _storage_account(project_id, key_name)
    encrypted = keyring.get_password(_KEYCHAIN_SERVICE, account)

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
    Uses the keyring-backed index (updated on every store/delete).
    This is the only vault function safe to expose to agent context.
    """
    return _get_index(project_id)


def delete_secret(project_id: str, key_name: str) -> bool:
    """
    Delete a single secret from the keychain.
    Returns True if deleted, False if not found.
    """
    account = _storage_account(project_id, key_name)
    existing = keyring.get_password(_KEYCHAIN_SERVICE, account)
    if not existing:
        return False

    try:
        keyring.delete_password(_KEYCHAIN_SERVICE, account)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            '{"event": "secret_delete_failed", "project_id": "%s", "key_name": "%s", "error": "%s"}',
            project_id,
            key_name,
            str(exc),
        )
        return False

    _remove_from_index(project_id, key_name)

    logger.info(
        '{"event": "secret_deleted", "project_id": "%s", "key_name": "%s"}',
        project_id,
        key_name,
    )
    return True


def delete_project_secrets(project_id: str) -> int:
    """Wipe all secrets for a project. Returns the count of deleted secrets."""
    keys = list_secret_keys(project_id)
    count = 0
    for key_name in list(keys):  # copy to avoid mutation during iteration
        if delete_secret(project_id, key_name):
            count += 1
    # Clear the index
    _set_index(project_id, [])
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
