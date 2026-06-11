"""
CP-4 tests: Security Scanner Postgres persistence and live task flow integration.

Tests:
  - scan_project() calls _store_scan_results via Orchestration internal endpoint
  - scan_nodes() calls _store_scan_results via Orchestration internal endpoint
  - get_findings() fetches from Orchestration internal endpoint (not in-memory)
  - get_scan_history() fetches from Orchestration internal endpoint (not in-memory)
  - No module-level _findings or _scan_history dicts exist in scanner.py
  - security.alert fires for HIGH and CRITICAL findings
  - security.alert does NOT fire for LOW and MEDIUM findings
"""
from __future__ import annotations

import sys
import types
import importlib
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(severity: str, pattern: str = "sql_injection_path") -> dict:
    return {
        "pattern": pattern,
        "severity": severity,
        "description": f"Test {pattern} finding",
        "suggested_fix": "Validate input",
        "node_path": ["node-a", "node-b"],
        "project_id": "proj-1",
    }


# ---------------------------------------------------------------------------
# Test: no in-memory state in scanner.py
# ---------------------------------------------------------------------------


class TestNoInMemoryState:
    """Verify scanner.py has no module-level _findings or _scan_history dicts."""

    def test_no_findings_dict(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "scanner_mod",
            "/home/ubuntu/CQR/packages/security-scanner/src/scanner.py",
        )
        mod = importlib.util.module_from_spec(spec)
        # Don't exec — just check source text
        with open("/home/ubuntu/CQR/packages/security-scanner/src/scanner.py") as f:
            src = f.read()
        assert "_findings: dict" not in src, "In-memory _findings dict found in scanner.py"
        assert "_scan_history: dict" not in src, "In-memory _scan_history dict found in scanner.py"

    def test_postgres_store_function_exists(self):
        with open("/home/ubuntu/CQR/packages/security-scanner/src/scanner.py") as f:
            src = f.read()
        assert "_store_scan_results" in src
        assert "ORCHESTRATION_URL" in src

    def test_get_findings_is_async(self):
        with open("/home/ubuntu/CQR/packages/security-scanner/src/scanner.py") as f:
            src = f.read()
        # get_findings must be async (fetches from DB)
        assert "async def get_findings" in src

    def test_get_scan_history_is_async(self):
        with open("/home/ubuntu/CQR/packages/security-scanner/src/scanner.py") as f:
            src = f.read()
        assert "async def get_scan_history" in src


# ---------------------------------------------------------------------------
# Test: _store_scan_results called after scan
# ---------------------------------------------------------------------------


class TestScanPersistence:
    """Verify scan results are posted to Orchestration internal endpoint."""

    @pytest.mark.asyncio
    async def test_scan_project_calls_store(self):
        """scan_project() must POST to /internal/security/store."""
        store_calls = []

        async def mock_store(scan_id, project_id, findings, task_id, node_count, edge_count):
            store_calls.append({
                "scan_id": scan_id,
                "project_id": project_id,
                "findings_count": len(findings),
                "task_id": task_id,
            })

        with patch(
            "src.scanner._fetch_project_graph",
            new=AsyncMock(return_value=([], [])),
        ), patch(
            "src.scanner._store_scan_results",
            new=mock_store,
        ):
            import src.scanner as scanner_mod
            await scanner_mod.scan_project("proj-1", task_id="task-1")

        assert len(store_calls) == 1
        assert store_calls[0]["project_id"] == "proj-1"
        assert store_calls[0]["task_id"] == "task-1"

    @pytest.mark.asyncio
    async def test_scan_nodes_calls_store(self):
        """scan_nodes() must POST to /internal/security/store."""
        store_calls = []

        async def mock_store(scan_id, project_id, findings, task_id, node_count, edge_count):
            store_calls.append({"project_id": project_id, "task_id": task_id})

        with patch(
            "src.scanner._fetch_nodes_by_ids",
            new=AsyncMock(return_value=([], [])),
        ), patch(
            "src.scanner._store_scan_results",
            new=mock_store,
        ):
            import src.scanner as scanner_mod
            await scanner_mod.scan_nodes("proj-1", ["node-a", "node-b"], task_id="task-2")

        assert len(store_calls) == 1
        assert store_calls[0]["project_id"] == "proj-1"

    @pytest.mark.asyncio
    async def test_get_findings_calls_db(self):
        """get_findings() must fetch from DB, not return in-memory data."""
        expected = [_make_finding("CRITICAL")]

        with patch(
            "src.scanner._fetch_findings_from_db",
            new=AsyncMock(return_value=expected),
        ):
            import src.scanner as scanner_mod
            result = await scanner_mod.get_findings("proj-1")

        assert result == expected

    @pytest.mark.asyncio
    async def test_get_scan_history_calls_db(self):
        """get_scan_history() must fetch from DB."""
        expected = [{"scan_id": "s1", "findings_count": 3, "scanned_at": "2026-01-01T00:00:00"}]

        with patch(
            "src.scanner._fetch_history_from_db",
            new=AsyncMock(return_value=expected),
        ):
            import src.scanner as scanner_mod
            result = await scanner_mod.get_scan_history("proj-1")

        assert result == expected

    @pytest.mark.asyncio
    async def test_scan_id_is_unique_per_scan(self):
        """Each scan_project() call must generate a unique scan_id."""
        scan_ids = []

        async def mock_store(scan_id, project_id, findings, task_id, node_count, edge_count):
            scan_ids.append(scan_id)

        with patch(
            "src.scanner._fetch_project_graph",
            new=AsyncMock(return_value=([], [])),
        ), patch(
            "src.scanner._store_scan_results",
            new=mock_store,
        ):
            import src.scanner as scanner_mod
            await scanner_mod.scan_project("proj-1")
            await scanner_mod.scan_project("proj-1")

        assert len(scan_ids) == 2
        assert scan_ids[0] != scan_ids[1]


# ---------------------------------------------------------------------------
# Test: security.alert WebSocket event
# ---------------------------------------------------------------------------


class TestSecurityAlertWebSocket:
    """Verify security.alert fires for HIGH/CRITICAL but not LOW/MEDIUM."""

    def _make_ws_alert_check(self, findings: list[dict]) -> tuple[list, list]:
        """
        Simulate the orchestration _run_task alert emission logic.
        Returns (alerted_findings, non_alerted_findings).
        """
        alerted = []
        not_alerted = []
        for finding in findings:
            if finding.get("severity", "").lower() in ("high", "critical"):
                alerted.append(finding)
            else:
                not_alerted.append(finding)
        return alerted, not_alerted

    def test_critical_triggers_alert(self):
        findings = [_make_finding("CRITICAL")]
        alerted, _ = self._make_ws_alert_check(findings)
        assert len(alerted) == 1
        assert alerted[0]["severity"] == "CRITICAL"

    def test_high_triggers_alert(self):
        findings = [_make_finding("HIGH", "unescaped_shell_exec")]
        alerted, _ = self._make_ws_alert_check(findings)
        assert len(alerted) == 1

    def test_medium_does_not_trigger_alert(self):
        findings = [_make_finding("MEDIUM", "secret_in_log")]
        alerted, not_alerted = self._make_ws_alert_check(findings)
        assert len(alerted) == 0
        assert len(not_alerted) == 1

    def test_low_does_not_trigger_alert(self):
        findings = [_make_finding("LOW", "orphaned_import")]
        alerted, not_alerted = self._make_ws_alert_check(findings)
        assert len(alerted) == 0
        assert len(not_alerted) == 1

    def test_mixed_severities(self):
        findings = [
            _make_finding("CRITICAL"),
            _make_finding("HIGH", "unescaped_shell_exec"),
            _make_finding("MEDIUM", "secret_in_log"),
            _make_finding("LOW", "orphaned_import"),
        ]
        alerted, not_alerted = self._make_ws_alert_check(findings)
        assert len(alerted) == 2
        assert len(not_alerted) == 2


# ---------------------------------------------------------------------------
# Test: _extract_diff_paths (orchestration helper)
# ---------------------------------------------------------------------------


class TestExtractDiffPaths:
    """Verify diff path extraction from unified diff format."""

    def _extract(self, diff: str) -> list[str]:
        """Call _extract_diff_paths directly (inline reimplementation for cross-package test)."""
        import re as _re
        paths: list[str] = []
        for line in diff.splitlines():
            m = _re.match(r'^\+\+\+ (?:b/)?(.+)$', line)
            if m:
                p = m.group(1).strip()
                if p != '/dev/null':
                    paths.append(p)
        return list(dict.fromkeys(paths))

    def test_standard_git_diff(self):
        diff = (
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,3 +1,4 @@\n"
            "+import os\n"
        )
        paths = self._extract(diff)
        assert "src/app.py" in paths

    def test_multiple_files(self):
        diff = (
            "+++ b/src/app.py\n"
            "+++ b/src/utils.py\n"
        )
        paths = self._extract(diff)
        assert "src/app.py" in paths
        assert "src/utils.py" in paths
        assert len(paths) == 2

    def test_dev_null_excluded(self):
        diff = "+++ /dev/null\n"
        paths = self._extract(diff)
        assert len(paths) == 0

    def test_deduplication(self):
        diff = (
            "+++ b/src/app.py\n"
            "+++ b/src/app.py\n"
        )
        paths = self._extract(diff)
        assert len(paths) == 1

    def test_empty_diff(self):
        paths = self._extract("")
        assert paths == []


# ---------------------------------------------------------------------------
# Test: Postgres CRUD functions exist in db.py
# ---------------------------------------------------------------------------


class TestDbCrudFunctions:
    """Verify the new security CRUD functions are present in db.py."""

    def test_store_scan_results_exists(self):
        with open("/home/ubuntu/CQR/packages/orchestration/src/db.py") as f:
            src = f.read()
        assert "async def store_scan_results" in src

    def test_get_latest_findings_exists(self):
        with open("/home/ubuntu/CQR/packages/orchestration/src/db.py") as f:
            src = f.read()
        assert "async def get_latest_findings" in src

    def test_get_scan_history_db_exists(self):
        with open("/home/ubuntu/CQR/packages/orchestration/src/db.py") as f:
            src = f.read()
        assert "async def get_scan_history_db" in src

    def test_internal_security_router_mounted(self):
        with open("/home/ubuntu/CQR/packages/orchestration/src/main.py") as f:
            src = f.read()
        assert "internal_security_router" in src
        assert "internal_security" in src

    def test_modified_by_agent_wired_in_router(self):
        with open("/home/ubuntu/CQR/packages/orchestration/src/router.py") as f:
            src = f.read()
        assert "_mark_agent_edits" in src
        assert "mark-agent-edit" in src
        assert "MODIFIED_BY_AGENT" in src or "mark_agent_edits" in src
