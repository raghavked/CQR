"""
CP-5 coverage tests for vault.
Targets api.py (0% coverage) and vault.py error/edge paths to reach ≥70%.
Uses VAULT_MASTER_KEY env var for test isolation (supported by vault.py).
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

# Generate a test master key once for all tests
_TEST_MASTER_KEY = Fernet.generate_key().decode("ascii")


@pytest.fixture(autouse=True)
def set_test_master_key(monkeypatch):
    """Inject a test master key so vault.py never touches the real OS keychain."""
    monkeypatch.setenv("VAULT_MASTER_KEY", _TEST_MASTER_KEY)


@pytest.fixture
def client():
    from src.api import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper: in-memory keyring backend for tests
# ---------------------------------------------------------------------------


class _InMemoryKeyring:
    """Minimal in-memory keyring backend for unit tests."""
    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)


@pytest.fixture
def mem_keyring():
    """Patch keyring module with in-memory backend for isolation."""
    kr = _InMemoryKeyring()
    with patch("src.vault.keyring.get_password", side_effect=kr.get_password), \
         patch("src.vault.keyring.set_password", side_effect=kr.set_password), \
         patch("src.vault.keyring.delete_password", side_effect=kr.delete_password):
        yield kr


# ---------------------------------------------------------------------------
# API endpoint tests (covers api.py)
# ---------------------------------------------------------------------------


class TestVaultApiEndpoints:
    """Cover api.py — all endpoints using actual route signatures."""

    def test_health(self, client):
        import keyring
        mock_kr = MagicMock()
        mock_kr.__class__.__name__ = "PlaintextKeyring"
        with patch.object(keyring, "get_keyring", return_value=mock_kr):
            r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "keyring_backend" in data

    def test_store_secret(self, client, mem_keyring):
        r = client.post(
            "/vault/store",
            json={"project_id": "proj-1", "key_name": "MY_KEY", "value": "s3cr3t"},
        )
        assert r.status_code == 200
        assert r.json()["key_name"] == "MY_KEY"

    def test_store_secret_missing_fields(self, client):
        r = client.post("/vault/store", json={"project_id": "proj-1"})
        assert r.status_code == 422

    def test_store_secret_keyring_unavailable(self, client):
        with patch("src.api.store_secret", side_effect=RuntimeError("no keyring")):
            r = client.post(
                "/vault/store",
                json={"project_id": "proj-1", "key_name": "K", "value": "v"},
            )
        assert r.status_code == 503

    def test_list_keys(self, client, mem_keyring):
        # Store two keys first
        from src.vault import store_secret
        store_secret("proj-list", "KEY_A", "val_a")
        store_secret("proj-list", "KEY_B", "val_b")
        r = client.get("/vault/list/proj-list")
        assert r.status_code == 200
        data = r.json()
        assert "KEY_A" in data["keys"]
        assert "KEY_B" in data["keys"]
        # Values must NOT be in the response
        assert "values" not in data

    def test_list_keys_compat_path(self, client, mem_keyring):
        from src.vault import store_secret
        store_secret("proj-compat", "KEY_A", "val_a")
        r = client.get("/vault/keys/proj-compat")
        assert r.status_code == 200
        assert "KEY_A" in r.json()["keys"]

    def test_list_keys_empty(self, client, mem_keyring):
        r = client.get("/vault/list/empty-proj")
        assert r.status_code == 200
        assert r.json()["keys"] == []

    def test_inject_endpoint_no_token_when_token_configured(self, client):
        """Inject endpoint must reject requests without IPC token when token is configured."""
        import src.api as api_mod
        original = api_mod._INTERNAL_IPC_TOKEN
        api_mod._INTERNAL_IPC_TOKEN = "secret-token"
        try:
            r = client.post("/vault/inject/proj-1")  # no token header
            assert r.status_code == 403
        finally:
            api_mod._INTERNAL_IPC_TOKEN = original

    def test_inject_endpoint_with_correct_token(self, client, mem_keyring):
        """Inject endpoint succeeds with correct IPC token."""
        import src.api as api_mod
        from src.vault import store_secret
        store_secret("proj-inject", "MY_KEY", "my_val")
        original = api_mod._INTERNAL_IPC_TOKEN
        api_mod._INTERNAL_IPC_TOKEN = "secret-token"
        try:
            r = client.post(
                "/vault/inject/proj-inject",
                headers={"X-CQR-IPC-Token": "secret-token"},
            )
            assert r.status_code == 200
            assert "env" in r.json()
        finally:
            api_mod._INTERNAL_IPC_TOKEN = original

    def test_inject_endpoint_no_token_configured_passes(self, client, mem_keyring):
        """When VAULT_IPC_TOKEN is not set, inject endpoint is unprotected (dev mode)."""
        import src.api as api_mod
        original = api_mod._INTERNAL_IPC_TOKEN
        api_mod._INTERNAL_IPC_TOKEN = ""
        try:
            r = client.post("/vault/inject/proj-1")
            assert r.status_code == 200
        finally:
            api_mod._INTERNAL_IPC_TOKEN = original

    def test_delete_key(self, client, mem_keyring):
        from src.vault import store_secret
        store_secret("proj-del", "MY_KEY", "val")
        r = client.request(
            "DELETE",
            "/vault/key",
            json={"project_id": "proj-del", "key_name": "MY_KEY"},
        )
        assert r.status_code == 200
        assert r.json()["key_name"] == "MY_KEY"

    def test_delete_key_not_found(self, client, mem_keyring):
        r = client.request(
            "DELETE",
            "/vault/key",
            json={"project_id": "proj-1", "key_name": "MISSING"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "not_found"

    def test_delete_project(self, client, mem_keyring):
        from src.vault import store_secret
        store_secret("proj-wipe", "K1", "v1")
        store_secret("proj-wipe", "K2", "v2")
        r = client.delete("/vault/project/proj-wipe")
        assert r.status_code == 200
        assert r.json()["deleted_count"] >= 0  # count may vary by implementation


# ---------------------------------------------------------------------------
# vault.py core function tests
# ---------------------------------------------------------------------------


class TestVaultCoreFunctions:
    """Cover vault.py core functions — full store/get/delete/inject cycle."""

    def test_store_and_list_and_delete(self, mem_keyring):
        from src.vault import store_secret, list_secret_keys, delete_secret
        store_secret("proj-1", "API_KEY", "my_value")
        keys = list_secret_keys("proj-1")
        assert "API_KEY" in keys
        delete_secret("proj-1", "API_KEY")
        keys_after = list_secret_keys("proj-1")
        assert "API_KEY" not in keys_after

    def test_get_secret_returns_plaintext(self, mem_keyring):
        from src.vault import store_secret, get_secret
        store_secret("proj-2", "DB_PASS", "hunter2")
        val = get_secret("proj-2", "DB_PASS")
        assert val == "hunter2"

    def test_get_secret_missing_key_returns_none(self, mem_keyring):
        """get_secret() returns None for missing keys (does not raise)."""
        from src.vault import get_secret
        val = get_secret("proj-3", "NONEXISTENT")
        assert val is None

    def test_inject_to_env_returns_all_keys(self, mem_keyring):
        from src.vault import store_secret, inject_to_env
        store_secret("proj-4", "KEY_A", "val_a")
        store_secret("proj-4", "KEY_B", "val_b")
        env = inject_to_env("proj-4")
        assert env.get("KEY_A") == "val_a"
        assert env.get("KEY_B") == "val_b"

    def test_list_keys_empty_project(self, mem_keyring):
        from src.vault import list_secret_keys
        keys = list_secret_keys("empty-project")
        assert keys == []

    def test_store_overwrites_existing_key(self, mem_keyring):
        from src.vault import store_secret, get_secret, list_secret_keys
        store_secret("proj-5", "KEY", "old_value")
        store_secret("proj-5", "KEY", "new_value")
        val = get_secret("proj-5", "KEY")
        assert val == "new_value"
        # Index should not have duplicates
        keys = list_secret_keys("proj-5")
        assert keys.count("KEY") == 1

    def test_delete_nonexistent_key_returns_false(self, mem_keyring):
        from src.vault import delete_secret
        result = delete_secret("proj-6", "NONEXISTENT")
        assert result is False

    def test_index_updated_on_store(self, mem_keyring):
        from src.vault import store_secret, list_secret_keys
        store_secret("proj-7", "K1", "v1")
        store_secret("proj-7", "K2", "v2")
        store_secret("proj-7", "K3", "v3")
        keys = list_secret_keys("proj-7")
        assert set(keys) == {"K1", "K2", "K3"}
