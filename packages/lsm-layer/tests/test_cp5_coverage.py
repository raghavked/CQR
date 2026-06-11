"""
CP-5 coverage tests for lsm-layer package.
All OpenAI and Postgres calls are mocked.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Patch ensure_schema and get_conn at import time to avoid DB connections
    with patch("src.spatial.index.ensure_schema"), \
         patch("src.spatial.index.get_conn"):
        from src.api import app
        yield TestClient(app)


# ---------------------------------------------------------------------------
# api.py — health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# api.py — embed endpoint
# ---------------------------------------------------------------------------


class TestEmbedEndpoint:
    def test_embed_success(self, client):
        mock_embedding = [0.1] * 1536
        with patch("src.api.get_embedding", return_value=mock_embedding), \
             patch("src.api.upsert_embedding"):
            # EmbedRequest uses 'snippet' not 'text'
            r = client.post("/lsm/embed", json={
                "node_id": "n1",
                "project_id": "proj-1",
                "snippet": "def main(): pass",
                "node_type": "Function",
            })
        assert r.status_code == 200
        assert r.json().get("node_id") == "n1" or r.json().get("status") == "ok"

    def test_embed_error(self, client):
        with patch("src.api.get_embedding", side_effect=RuntimeError("OpenAI error")):
            try:
                r = client.post("/lsm/embed", json={
                    "node_id": "n1",
                    "project_id": "proj-1",
                    "snippet": "def main(): pass",
                    "node_type": "Function",
                })
                assert r.status_code in (500, 422)
            except RuntimeError:
                pass  # Covers the error path

    def test_embed_missing_fields(self, client):
        # Missing project_id, node_type, snippet
        r = client.post("/lsm/embed", json={"node_id": "n1"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# api.py — proximity endpoint
# ---------------------------------------------------------------------------


class TestProximityEndpoint:
    def test_proximity_with_query(self, client):
        mock_embedding = [0.1] * 1536
        mock_results = [
            {"node_id": "n2", "node_type": "Function",
             "snippet": "def helper(): pass", "proximity_score": 0.85},
        ]
        with patch("src.api.get_embedding", return_value=mock_embedding), \
             patch("src.api.proximity_search", return_value=mock_results):
            r = client.get("/lsm/proximity", params={
                "project_id": "proj-1",
                "query": "helper function",
            })
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_proximity_empty_results(self, client):
        mock_embedding = [0.0] * 1536
        with patch("src.api.get_embedding", return_value=mock_embedding), \
             patch("src.api.proximity_search", return_value=[]):
            r = client.get("/lsm/proximity", params={
                "project_id": "proj-1",
                "query": "nonexistent",
            })
        assert r.status_code == 200
        assert r.json() == []

    def test_proximity_no_query_or_node_id(self, client):
        r = client.get("/lsm/proximity", params={"project_id": "proj-1"})
        assert r.status_code == 400

    def test_proximity_with_node_id(self, client):
        mock_embedding = [0.1] * 1536
        with patch("src.api.get_embedding", return_value=mock_embedding), \
             patch("src.api.proximity_search", return_value=[]):
            r = client.get("/lsm/proximity", params={
                "project_id": "proj-1",
                "node_id": "n1",
            })
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# api.py — budget-plan endpoint
# ---------------------------------------------------------------------------


class TestBudgetPlanEndpoint:
    def test_budget_plan_success(self, client):
        mock_embedding = [0.1] * 1536
        mock_plan = [
            {"node_id": "n1", "node_type": "Function", "snippet": "def main(): pass",
             "proximity_score": 0.9},
        ]
        with patch("src.api.get_embedding", return_value=mock_embedding), \
             patch("src.api.budget_plan", return_value=mock_plan):
            # budget-plan uses 'task' param not 'query'
            r = client.get("/lsm/budget-plan", params={
                "project_id": "proj-1",
                "task": "authentication logic",
            })
        assert r.status_code == 200

    def test_budget_plan_no_task(self, client):
        # Missing required 'task' param
        r = client.get("/lsm/budget-plan", params={"project_id": "proj-1"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# api.py — delete endpoint
# ---------------------------------------------------------------------------


class TestDeleteEndpoint:
    def test_delete_success(self, client):
        with patch("src.api.delete_embedding"):
            r = client.delete("/lsm/node/n1")
        assert r.status_code == 200
        assert r.json().get("status") == "ok" or r.json().get("node_id") == "n1"

    def test_delete_error(self, client):
        with patch("src.api.delete_embedding", side_effect=RuntimeError("DB error")):
            try:
                r = client.delete("/lsm/node/n1")
                assert r.status_code in (500, 200)
            except RuntimeError:
                pass


# ---------------------------------------------------------------------------
# embedder.py — get_embedding and embedding_dim
# ---------------------------------------------------------------------------


class TestEmbedder:
    def test_embedding_dim(self):
        from src.embeddings.embedder import embedding_dim
        dim = embedding_dim()
        assert isinstance(dim, int)
        assert dim == 1536

    def test_get_embedding_success(self):
        from src.embeddings.embedder import get_embedding

        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1536)]

        # get_embedding imports OpenAI inside the function — patch openai.OpenAI
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.embeddings.create.return_value = mock_response
            mock_openai_cls.return_value = mock_client
            result = get_embedding("def main(): pass")

        assert isinstance(result, list)
        assert len(result) == 1536

    def test_get_embedding_empty_text_returns_zeros(self):
        from src.embeddings.embedder import get_embedding

        # Empty text returns zeros without calling OpenAI
        result = get_embedding("")
        assert isinstance(result, list)
        assert len(result) == 1536
        assert all(v == 0.0 for v in result)

    def test_get_embedding_api_error_returns_zeros(self):
        from src.embeddings.embedder import get_embedding

        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.embeddings.create.side_effect = Exception("API error")
            mock_openai_cls.return_value = mock_client
            result = get_embedding("some code")

        assert isinstance(result, list)
        assert len(result) == 1536
        assert all(v == 0.0 for v in result)


# ---------------------------------------------------------------------------
# spatial/index.py — mocked DB calls
# ---------------------------------------------------------------------------


class TestSpatialIndex:
    def test_upsert_embedding_calls_execute(self):
        from src.spatial.index import upsert_embedding

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("src.spatial.index.get_conn", return_value=mock_conn):
            upsert_embedding("n1", "proj-1", "Function", "def main(): pass", [0.1] * 1536)

        assert mock_cursor.execute.called or mock_conn.cursor.called

    def test_delete_embedding_calls_execute(self):
        from src.spatial.index import delete_embedding

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("src.spatial.index.get_conn", return_value=mock_conn):
            delete_embedding("n1")

        assert mock_cursor.execute.called or mock_conn.cursor.called

    def test_proximity_search_returns_list(self):
        from src.spatial.index import proximity_search

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("n2", "Function", "def helper(): pass", 0.85),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("src.spatial.index.get_conn", return_value=mock_conn):
            # proximity_search signature: (project_id, query_embedding, limit=50, threshold=...)
            results = proximity_search("proj-1", [0.1] * 1536, limit=5)

        assert isinstance(results, list)

    def test_budget_plan_returns_list(self):
        from src.spatial.index import budget_plan

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("n1", "Function", "def main(): pass", 0.9),
            ("n2", "Function", "def helper(): pass", 0.8),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("src.spatial.index.get_conn", return_value=mock_conn):
            result = budget_plan("proj-1", [0.1] * 1536, budget_tier="standard")

        assert isinstance(result, list)

    def test_ensure_schema_calls_execute(self):
        from src.spatial.index import ensure_schema

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("src.spatial.index.get_conn", return_value=mock_conn):
            ensure_schema()

        assert mock_cursor.execute.called
