"""
Unit tests for the KG graph path traversal engine and pattern classifier.

These tests use synthetic in-memory node/edge graphs — no KG engine or
network calls are required. They verify the core traversal logic in isolation.
"""
from __future__ import annotations

import pytest

from src.traversal import (
    MAX_DEPTH,
    is_sink,
    is_source,
    is_validation,
    scan_graph,
    traverse_paths,
)
from src.patterns import (
    check_circular_dependencies,
    check_hardcoded_credentials,
    check_orphaned_imports,
    classify_traversal_result,
)


# ---------------------------------------------------------------------------
# Helpers to build synthetic nodes and edges
# ---------------------------------------------------------------------------


def fn_node(node_id: str, name: str, signature: str = "", docstring: str = "") -> dict:
    return {
        "id": node_id,
        "type": "Function",
        "properties": {"name": name, "signature": signature, "docstring": docstring},
    }


def env_ref_node(node_id: str, key_name: str) -> dict:
    return {
        "id": node_id,
        "type": "EnvRef",
        "properties": {"name": key_name, "key_name": key_name, "signature": ""},
    }


def import_node(node_id: str, module: str) -> dict:
    return {
        "id": node_id,
        "type": "Import",
        "properties": {"name": module, "imported_module": module, "signature": ""},
    }


def file_node(node_id: str, path: str) -> dict:
    return {
        "id": node_id,
        "type": "File",
        "properties": {"name": path, "path": path, "signature": ""},
    }


def calls_edge(from_id: str, to_id: str) -> dict:
    return {"from_id": from_id, "to_id": to_id, "edge_type": "CALLS"}


def imports_edge(from_id: str, to_id: str) -> dict:
    return {"from_id": from_id, "to_id": to_id, "edge_type": "IMPORTS"}


# ---------------------------------------------------------------------------
# Node classification tests
# ---------------------------------------------------------------------------


class TestNodeClassification:
    def test_env_ref_is_source(self):
        node = env_ref_node("e1", "DATABASE_URL")
        assert is_source(node) is True

    def test_user_input_function_is_source(self):
        node = fn_node("f1", "parse_request", signature="def parse_request(body):")
        assert is_source(node) is True

    def test_regular_function_is_not_source(self):
        node = fn_node("f2", "calculate_total", signature="def calculate_total(items):")
        assert is_source(node) is False

    def test_sql_execute_is_sink(self):
        node = fn_node("f3", "run_query", signature="cursor.execute(sql)")
        flag, sink_type = is_sink(node)
        assert flag is True
        assert sink_type == "sql_execute"

    def test_subprocess_is_sink(self):
        node = fn_node("f4", "run_cmd", signature="subprocess.run(cmd)")
        flag, sink_type = is_sink(node)
        assert flag is True
        assert sink_type == "shell_execute"

    def test_file_write_is_sink(self):
        node = fn_node("f5", "save_file", signature="open(path, 'w')")
        flag, sink_type = is_sink(node)
        assert flag is True
        assert sink_type == "file_write"

    def test_logger_is_sink(self):
        node = fn_node("f6", "log_event", signature="logger.info(msg)")
        flag, sink_type = is_sink(node)
        assert flag is True
        assert sink_type == "log_output"

    def test_validate_is_validation(self):
        node = fn_node("f7", "validate_input", signature="def validate_input(x):")
        assert is_validation(node) is True

    def test_sanitize_is_validation(self):
        node = fn_node("f8", "sanitize_sql", signature="def sanitize_sql(query):")
        assert is_validation(node) is True

    def test_regular_function_is_not_validation(self):
        node = fn_node("f9", "format_date", signature="def format_date(dt):")
        assert is_validation(node) is False


# ---------------------------------------------------------------------------
# Traversal tests
# ---------------------------------------------------------------------------


class TestTraversal:
    def test_direct_unvalidated_path(self):
        """EnvRef → sql_execute with no validation node should be flagged."""
        nodes = [
            env_ref_node("src", "DB_URL"),
            fn_node("sink", "execute_query", signature="cursor.execute(sql)"),
        ]
        edges = [calls_edge("src", "sink")]

        results = scan_graph(nodes, edges)
        assert len(results) == 1
        assert results[0]["sink_type"] == "sql_execute"
        assert results[0]["path"] == ["src", "sink"]
        assert results[0]["validated"] is False

    def test_validated_path_not_flagged(self):
        """EnvRef → validate → sql_execute should NOT be flagged."""
        nodes = [
            env_ref_node("src", "DB_URL"),
            fn_node("val", "validate_input", signature="def validate_input(x):"),
            fn_node("sink", "execute_query", signature="cursor.execute(sql)"),
        ]
        edges = [calls_edge("src", "val"), calls_edge("val", "sink")]

        results = scan_graph(nodes, edges)
        assert len(results) == 0

    def test_multi_hop_unvalidated_path(self):
        """Source → intermediate → sink (3 hops, no validation) should be flagged."""
        nodes = [
            env_ref_node("src", "SECRET_KEY"),
            fn_node("mid", "process_key", signature="def process_key(k):"),
            fn_node("sink", "subprocess.run(cmd)", signature="subprocess.run(cmd)"),
        ]
        edges = [calls_edge("src", "mid"), calls_edge("mid", "sink")]

        results = scan_graph(nodes, edges)
        assert len(results) == 1
        assert results[0]["path"] == ["src", "mid", "sink"]
        assert results[0]["sink_type"] == "shell_execute"

    def test_validation_mid_path_prunes(self):
        """Source → validate → intermediate → sink: validation mid-path should prune."""
        nodes = [
            env_ref_node("src", "API_KEY"),
            fn_node("val", "sanitize_value", signature="def sanitize_value(v):"),
            fn_node("mid", "build_query", signature="def build_query(v):"),
            fn_node("sink", "cursor.execute", signature="cursor.execute(sql)"),
        ]
        edges = [
            calls_edge("src", "val"),
            calls_edge("val", "mid"),
            calls_edge("mid", "sink"),
        ]

        results = scan_graph(nodes, edges)
        assert len(results) == 0

    def test_cycle_does_not_infinite_loop(self):
        """Cyclic CALLS graph should terminate without infinite loop."""
        nodes = [
            env_ref_node("src", "TOKEN"),
            fn_node("a", "func_a", signature="def func_a():"),
            fn_node("b", "func_b", signature="def func_b():"),
        ]
        edges = [
            calls_edge("src", "a"),
            calls_edge("a", "b"),
            calls_edge("b", "a"),  # cycle
        ]
        # Should not raise or hang
        results = scan_graph(nodes, edges)
        # No sink in this graph, so no findings
        assert isinstance(results, list)

    def test_max_depth_respected(self):
        """Paths longer than MAX_DEPTH should not be traversed."""
        # Build a chain of MAX_DEPTH + 2 nodes
        chain_nodes = [env_ref_node("src", "KEY")]
        chain_edges = []
        prev = "src"
        for i in range(MAX_DEPTH + 2):
            nid = f"n{i}"
            chain_nodes.append(fn_node(nid, f"func_{i}", signature="def func():"))
            chain_edges.append(calls_edge(prev, nid))
            prev = nid
        # Add a sink at the very end (beyond MAX_DEPTH)
        chain_nodes.append(fn_node("far_sink", "execute", signature="cursor.execute(sql)"))
        chain_edges.append(calls_edge(prev, "far_sink"))

        results = scan_graph(chain_nodes, chain_edges)
        # The sink is beyond MAX_DEPTH — should not be reached
        assert len(results) == 0

    def test_deduplication(self):
        """Multiple paths to the same sink should produce only one finding."""
        nodes = [
            env_ref_node("src", "KEY"),
            fn_node("path_a", "func_a", signature="def func_a():"),
            fn_node("path_b", "func_b", signature="def func_b():"),
            fn_node("sink", "cursor.execute", signature="cursor.execute(sql)"),
        ]
        edges = [
            calls_edge("src", "path_a"),
            calls_edge("src", "path_b"),
            calls_edge("path_a", "sink"),
            calls_edge("path_b", "sink"),
        ]
        results = scan_graph(nodes, edges)
        # Two paths to the same sink from the same source → deduplicated to 1
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Pattern classification tests
# ---------------------------------------------------------------------------


class TestPatternClassification:
    def test_env_ref_to_log_is_secret_in_log(self):
        result = {
            "source_id": "src",
            "source_type": "EnvRef",
            "path": ["src", "sink"],
            "sink_type": "log_output",
            "validated": False,
        }
        finding = classify_traversal_result(result, "proj1")
        assert finding is not None
        assert finding["pattern"] == "secret_in_log"
        assert finding["severity"] == "high"

    def test_env_ref_to_sql_is_sql_injection(self):
        result = {
            "source_id": "src",
            "source_type": "EnvRef",
            "path": ["src", "sink"],
            "sink_type": "sql_execute",
            "validated": False,
        }
        finding = classify_traversal_result(result, "proj1")
        assert finding is not None
        assert finding["pattern"] == "sql_injection_path"
        assert finding["severity"] == "critical"

    def test_user_input_to_shell_is_unescaped_shell(self):
        result = {
            "source_id": "src",
            "source_type": "Function",
            "path": ["src", "sink"],
            "sink_type": "shell_execute",
            "validated": False,
        }
        finding = classify_traversal_result(result, "proj1")
        assert finding is not None
        assert finding["pattern"] == "unescaped_shell_exec"
        assert finding["severity"] == "critical"

    def test_env_ref_to_other_sink_is_unvalidated_env_ref(self):
        result = {
            "source_id": "src",
            "source_type": "EnvRef",
            "path": ["src", "sink"],
            "sink_type": "file_write",
            "validated": False,
        }
        finding = classify_traversal_result(result, "proj1")
        assert finding is not None
        assert finding["pattern"] == "unvalidated_env_ref"
        assert finding["severity"] == "medium"

    def test_finding_has_node_path(self):
        result = {
            "source_id": "n1",
            "source_type": "EnvRef",
            "path": ["n1", "n2", "n3"],
            "sink_type": "sql_execute",
            "validated": False,
        }
        finding = classify_traversal_result(result, "proj1")
        assert finding["node_path"] == ["n1", "n2", "n3"]


# ---------------------------------------------------------------------------
# Structural check tests
# ---------------------------------------------------------------------------


class TestStructuralChecks:
    def test_hardcoded_credential_detected(self):
        nodes = [
            fn_node("f1", "setup", signature='api_key = "sk-1234567890abcdef1234567890abcdef"'),
        ]
        findings = check_hardcoded_credentials(nodes, "proj1")
        assert len(findings) == 1
        assert findings[0]["pattern"] == "hardcoded_credential"

    def test_no_false_positive_for_clean_node(self):
        nodes = [
            fn_node("f1", "setup", signature="api_key = os.environ.get('API_KEY')"),
        ]
        findings = check_hardcoded_credentials(nodes, "proj1")
        assert len(findings) == 0

    def test_orphaned_import_detected(self):
        nodes = [
            import_node("i1", "requests"),
            fn_node("f1", "do_work", signature="def do_work():"),
        ]
        # Only f1 is targeted by an edge — i1 is orphaned
        edges = [calls_edge("f1", "f1")]  # self-call, not targeting i1
        findings = check_orphaned_imports(nodes, edges, "proj1")
        assert any(f["pattern"] == "orphaned_import" for f in findings)

    def test_used_import_not_flagged(self):
        nodes = [
            import_node("i1", "requests"),
            fn_node("f1", "do_work", signature="def do_work():"),
        ]
        # i1 is targeted by an edge — it is used
        edges = [calls_edge("f1", "i1")]
        findings = check_orphaned_imports(nodes, edges, "proj1")
        assert len(findings) == 0

    def test_circular_dependency_detected(self):
        nodes = [
            file_node("file_a", "a.py"),
            file_node("file_b", "b.py"),
        ]
        edges = [
            imports_edge("file_a", "file_b"),
            imports_edge("file_b", "file_a"),  # cycle
        ]
        findings = check_circular_dependencies(nodes, edges, "proj1")
        assert len(findings) >= 1
        assert findings[0]["pattern"] == "circular_dependency"

    def test_no_circular_dependency_in_dag(self):
        nodes = [
            file_node("file_a", "a.py"),
            file_node("file_b", "b.py"),
            file_node("file_c", "c.py"),
        ]
        edges = [
            imports_edge("file_a", "file_b"),
            imports_edge("file_b", "file_c"),
        ]
        findings = check_circular_dependencies(nodes, edges, "proj1")
        assert len(findings) == 0
