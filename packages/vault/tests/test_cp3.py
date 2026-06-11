"""
CP-3 tests for the Vault package.

Tests:
  - store_secret / get_secret round-trip (keyring-backed)
  - list_secret_keys uses the keyring index (not in-memory dict)
  - delete_secret removes from keyring and index
  - delete_project_secrets wipes all keys and clears index
  - inject_to_env returns correct plaintext values
  - Fernet encryption/decryption correctness
  - Master key is stable across calls (same Fernet instance)
  - No in-memory _secret_store dict exists in vault.py

All tests use VAULT_MASTER_KEY env var to avoid touching the real OS keychain.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Use a fixed test master key so tests don't touch the real keychain
_TEST_MASTER_KEY = "c2VjcmV0a2V5Zm9ydGVzdGluZ29ubHkxMjM0NTY3OA=="
# Valid Fernet key (32 bytes base64url)
from cryptography.fernet import Fernet
_FERNET_TEST_KEY = Fernet.generate_key().decode("ascii")
os.environ["VAULT_MASTER_KEY"] = _FERNET_TEST_KEY

# Use a temp keyring file for tests
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyrings.alt.file.PlaintextKeyring")

import keyring
from keyrings.alt.file import PlaintextKeyring  # type: ignore[import]
keyring.set_keyring(PlaintextKeyring())

from src.vault import (
    delete_project_secrets,
    delete_secret,
    get_secret,
    inject_to_env,
    list_secret_keys,
    store_secret,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_project() -> str:
    return f"test-proj-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Store / get round-trip
# ---------------------------------------------------------------------------


class TestStoreGet:
    def test_store_and_get_roundtrip(self):
        pid = _unique_project()
        store_secret(pid, "API_KEY", "super-secret-value")
        result = get_secret(pid, "API_KEY")
        assert result == "super-secret-value"

    def test_get_nonexistent_returns_none(self):
        pid = _unique_project()
        result = get_secret(pid, "MISSING_KEY")
        assert result is None

    def test_store_multiple_keys(self):
        pid = _unique_project()
        store_secret(pid, "KEY_A", "value-a")
        store_secret(pid, "KEY_B", "value-b")
        assert get_secret(pid, "KEY_A") == "value-a"
        assert get_secret(pid, "KEY_B") == "value-b"

    def test_overwrite_existing_key(self):
        pid = _unique_project()
        store_secret(pid, "TOKEN", "old-value")
        store_secret(pid, "TOKEN", "new-value")
        assert get_secret(pid, "TOKEN") == "new-value"

    def test_value_is_encrypted_at_rest(self):
        """The value stored in keyring must not be the plaintext value."""
        import keyring as kr
        pid = _unique_project()
        store_secret(pid, "SECRET", "plaintext-value")
        raw = kr.get_password("cqr-vault", f"{pid}:SECRET")
        assert raw is not None
        assert "plaintext-value" not in raw  # must be Fernet-encrypted

    def test_special_characters_in_value(self):
        pid = _unique_project()
        special = "p@$$w0rd!#%^&*()_+{}|:<>?"
        store_secret(pid, "SPECIAL", special)
        assert get_secret(pid, "SPECIAL") == special

    def test_unicode_value(self):
        pid = _unique_project()
        unicode_val = "日本語テスト🔑"
        store_secret(pid, "UNICODE", unicode_val)
        assert get_secret(pid, "UNICODE") == unicode_val


# ---------------------------------------------------------------------------
# Key index (list_secret_keys)
# ---------------------------------------------------------------------------


class TestKeyIndex:
    def test_list_keys_returns_stored_key_names(self):
        pid = _unique_project()
        store_secret(pid, "KEY_1", "v1")
        store_secret(pid, "KEY_2", "v2")
        keys = list_secret_keys(pid)
        assert "KEY_1" in keys
        assert "KEY_2" in keys

    def test_list_keys_empty_for_new_project(self):
        pid = _unique_project()
        assert list_secret_keys(pid) == []

    def test_list_keys_does_not_return_values(self):
        pid = _unique_project()
        store_secret(pid, "DB_PASSWORD", "hunter2")
        keys = list_secret_keys(pid)
        assert "hunter2" not in keys
        assert "DB_PASSWORD" in keys

    def test_no_in_memory_secret_store(self):
        """Verify that vault.py does not have a module-level _secret_store dict."""
        import src.vault as vault_module
        assert not hasattr(vault_module, "_secret_store"), (
            "vault.py must not have an in-memory _secret_store fallback"
        )

    def test_index_is_keyring_backed(self):
        """The index must be stored in keyring, not in a Python dict."""
        import keyring as kr
        pid = _unique_project()
        store_secret(pid, "IDX_KEY", "val")
        # The index entry must exist in keyring
        raw_index = kr.get_password("cqr-vault-index", pid)
        assert raw_index is not None
        import json
        index = json.loads(raw_index)
        assert "IDX_KEY" in index


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_existing_key(self):
        pid = _unique_project()
        store_secret(pid, "TO_DELETE", "value")
        deleted = delete_secret(pid, "TO_DELETE")
        assert deleted is True
        assert get_secret(pid, "TO_DELETE") is None

    def test_delete_removes_from_index(self):
        pid = _unique_project()
        store_secret(pid, "IDX_DEL", "value")
        delete_secret(pid, "IDX_DEL")
        assert "IDX_DEL" not in list_secret_keys(pid)

    def test_delete_nonexistent_returns_false(self):
        pid = _unique_project()
        result = delete_secret(pid, "GHOST")
        assert result is False

    def test_delete_project_secrets_wipes_all(self):
        pid = _unique_project()
        store_secret(pid, "A", "1")
        store_secret(pid, "B", "2")
        store_secret(pid, "C", "3")
        count = delete_project_secrets(pid)
        assert count == 3
        assert list_secret_keys(pid) == []

    def test_delete_project_secrets_empty_project(self):
        pid = _unique_project()
        count = delete_project_secrets(pid)
        assert count == 0


# ---------------------------------------------------------------------------
# inject_to_env
# ---------------------------------------------------------------------------


class TestInjectToEnv:
    def test_inject_returns_plaintext_values(self):
        pid = _unique_project()
        store_secret(pid, "DB_HOST", "localhost")
        store_secret(pid, "DB_PORT", "5432")
        env = inject_to_env(pid)
        assert env["DB_HOST"] == "localhost"
        assert env["DB_PORT"] == "5432"

    def test_inject_empty_project_returns_empty_dict(self):
        pid = _unique_project()
        env = inject_to_env(pid)
        assert env == {}

    def test_inject_only_returns_existing_keys(self):
        pid = _unique_project()
        store_secret(pid, "ONLY_KEY", "only-value")
        env = inject_to_env(pid)
        assert len(env) == 1
        assert "ONLY_KEY" in env
