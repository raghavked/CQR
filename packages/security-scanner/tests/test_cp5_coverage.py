"""
CP-5 coverage tests for security-scanner.
Targets the uncovered lines in scanner.py and api.py to reach ≥70% total.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# API endpoint tests (covers api.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from src.api import app
    return TestClient(app)


class TestApiEndpoints:
    """Cover api.py endpoints."""

    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_scan_endpoint(self, client):
        with patch("src.api.scan_project", new=AsyncMock(return_value=[])):
            r = client.post("/security/scan", json={"project_id": "proj-1"})
        assert r.status_code == 200
        data = r.json()
        assert data["project_id"] == "proj-1"
        assert data["findings_count"] == 0

    def test_scan_with_task_id(self, client):
        with patch("src.api.scan_project", new=AsyncMock(return_value=[])):
            r = client.post("/security/scan", json={"project_id": "proj-1", "task_id": "task-1"})
        assert r.status_code == 200

    def test_scan_nodes_endpoint(self, client):
        with patch("src.api.scan_nodes", new=AsyncMock(return_value=[])):
            r = client.post(
                "/security/scan-nodes",
                json={"project_id": "proj-1", "node_ids": ["n1", "n2"]},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["scanned_node_ids"] == ["n1", "n2"]

    def test_scan_nodes_empty_ids_returns_400(self, client):
        r = client.post(
            "/security/scan-nodes",
            json={"project_id": "proj-1", "node_ids": []},
        )
        assert r.status_code == 400

    def test_get_report_endpoint(self, client):
        findings = [{"pattern": "sql_injection_path", "severity": "CRITICAL", "node_path": ["a", "b"]}]
        with patch("src.api.get_findings", new=AsyncMock(return_value=findings)):
            r = client.get("/security/report/proj-1")
        assert r.status_code == 200
        assert r.json()["findings_count"] == 1

    def test_get_history_endpoint(self, client):
        history = [{"scan_id": "s1", "findings_count": 2, "scanned_at": "2026-01-01T00:00:00"}]
        with patch("src.api.get_scan_history", new=AsyncMock(return_value=history)):
            r = client.get("/security/history/proj-1")
        assert r.status_code == 200
        assert r.json()["scan_count"] == 1


# ---------------------------------------------------------------------------
# Scanner.py async path tests
# ---------------------------------------------------------------------------


class TestScannerAsyncPaths:
    """Cover scanner.py fetch/store paths."""

    @pytest.mark.asyncio
    async def test_fetch_project_graph_returns_empty_on_error(self):
        """_fetch_project_graph returns ([], []) when KG is unreachable."""
        import src.scanner as scanner_mod
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value = mock_client
            nodes, edges = await scanner_mod._fetch_project_graph("proj-1")
        assert nodes == []
        assert edges == []

    @pytest.mark.asyncio
    async def test_store_scan_results_handles_error_gracefully(self):
        """_store_scan_results logs warning and does not raise on network error."""
        import src.scanner as scanner_mod
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value = mock_client
            # Should not raise
            await scanner_mod._store_scan_results(
                scan_id="s1",
                project_id="proj-1",
                findings=[],
                task_id=None,
                node_count=0,
                edge_count=0,
            )

    @pytest.mark.asyncio
    async def test_fetch_findings_from_db_returns_empty_on_error(self):
        """_fetch_findings_from_db returns [] on network error."""
        import src.scanner as scanner_mod
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value = mock_client
            result = await scanner_mod._fetch_findings_from_db("proj-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_history_from_db_returns_empty_on_error(self):
        """_fetch_history_from_db returns [] on network error."""
        import src.scanner as scanner_mod
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value = mock_client
            result = await scanner_mod._fetch_history_from_db("proj-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_scan_project_with_findings(self):
        """scan_project returns findings and calls store."""
        store_calls = []

        async def mock_store(scan_id, project_id, findings, task_id, node_count, edge_count):
            store_calls.append(len(findings))

        # Build a minimal graph with a taint path
        nodes = [
            {"id": "env1", "type": "EnvRef", "properties": {"name": "SECRET", "project_id": "p1"}},
            {"id": "fn1", "type": "Function", "properties": {"name": "cursor.execute", "signature": "cursor.execute(sql)", "project_id": "p1"}},
        ]
        edges = [{"from_id": "env1", "to_id": "fn1", "edge_type": "CALLS"}]

        with patch("src.scanner._fetch_project_graph", new=AsyncMock(return_value=(nodes, edges))), \
             patch("src.scanner._store_scan_results", new=mock_store):
            import src.scanner as scanner_mod
            findings = await scanner_mod.scan_project("p1", task_id="t1")

        assert isinstance(findings, list)
        assert len(store_calls) == 1

    @pytest.mark.asyncio
    async def test_scan_nodes_deduplicates_subgraph(self):
        """scan_nodes merges overlapping subgraph results correctly."""
        store_calls = []

        async def mock_store(scan_id, project_id, findings, task_id, node_count, edge_count):
            store_calls.append({"node_count": node_count})

        # Two node IDs that return the same subgraph node (overlap)
        subgraph = {"nodes": [{"id": "n1", "type": "File", "properties": {}}], "edges": []}

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = AsyncMock()
            mock_resp.json = lambda: subgraph
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            with patch("src.scanner._store_scan_results", new=mock_store):
                import src.scanner as scanner_mod
                await scanner_mod.scan_nodes("p1", ["n1", "n2"])

        # Deduplication: only 1 unique node despite 2 subgraph fetches
        assert store_calls[0]["node_count"] == 1

    def test_normalise_nodes_strips_prefix(self):
        """_normalise_nodes strips 'n.' prefix from Kuzu property keys."""
        import src.scanner as scanner_mod
        nodes = [{"id": "n1", "type": "Function", "properties": {"n.name": "foo", "n.signature": "def foo():"}}]
        result = scanner_mod._normalise_nodes(nodes)
        assert "name" in result[0]["properties"]
        assert "signature" in result[0]["properties"]
        assert "n.name" not in result[0]["properties"]

    def test_normalise_nodes_no_prefix_unchanged(self):
        """_normalise_nodes leaves keys without 'n.' prefix unchanged."""
        import src.scanner as scanner_mod
        nodes = [{"id": "n1", "type": "File", "properties": {"path": "/src/app.py"}}]
        result = scanner_mod._normalise_nodes(nodes)
        assert result[0]["properties"]["path"] == "/src/app.py"
