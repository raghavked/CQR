"""
Go Tree-sitter parser for KG ingestion.

Extracts the same node types as the Python parser:
  Function, Class (mapped from struct/interface), Import, Variable, EnvRef

Handles .go files.

Go-specific mapping:
  Function  → function_declaration, method_declaration
  Class     → type_declaration (struct_type, interface_type)
  Import    → import_declaration
  EnvRef    → os.Getenv("KEY") and os.LookupEnv("KEY") calls
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GO_LANGUAGE = None
_GO_PARSER = None

# Pattern for os.Getenv / os.LookupEnv calls
_ENV_PATTERNS = re.compile(
    r'os\.(?:Getenv|LookupEnv)\(\s*"([A-Z_][A-Z0-9_]*)"\s*\)',
    re.MULTILINE,
)


def _get_parser():
    """Lazy-initialise the Tree-sitter Go parser."""
    global _GO_LANGUAGE, _GO_PARSER
    if _GO_PARSER is None:
        import tree_sitter_go as tsgo
        from tree_sitter import Language, Parser

        _GO_LANGUAGE = Language(tsgo.language())
        _GO_PARSER = Parser(_GO_LANGUAGE)
    return _GO_PARSER, _GO_LANGUAGE


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_doc_comment(node, source: bytes) -> str:
    """Extract Go doc comment (// comment block) immediately preceding a node."""
    prev = node.prev_sibling
    if prev and prev.type == "comment":
        return _node_text(prev, source).strip()
    return ""


def _extract_functions(tree_root, source: bytes, file_path: str) -> list[dict[str, Any]]:
    """Extract function and method declarations."""
    functions = []

    def visit(node):
        if node.type in ("function_declaration", "method_declaration"):
            name = ""
            receiver = ""

            for child in node.children:
                if child.type == "identifier" and not name:
                    name = _node_text(child, source)
                # Method receiver: (r *ReceiverType)
                if child.type == "parameter_list" and not receiver:
                    receiver = _node_text(child, source)

            params = ""
            for child in node.children:
                if child.type == "parameter_list":
                    params = _node_text(child, source)
                    break

            result_type = ""
            for child in node.children:
                if child.type in ("type_identifier", "pointer_type", "qualified_type", "parameter_list"):
                    # The last parameter_list is the result type for multi-return
                    pass

            if name:
                signature = f"func {receiver}{name}{params}"
                functions.append({
                    "name": name,
                    "file_path": file_path,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "signature": signature,
                    "docstring": _get_doc_comment(node, source),
                })

        for child in node.children:
            visit(child)

    visit(tree_root)
    return functions


def _extract_classes(tree_root, source: bytes, file_path: str) -> list[dict[str, Any]]:
    """
    Extract struct and interface type declarations.
    These are mapped to Class nodes in the KG since Go has no class keyword.
    """
    classes = []

    def visit(node):
        if node.type == "type_declaration":
            for child in node.children:
                if child.type == "type_spec":
                    name = ""
                    kind = ""
                    for sub in child.children:
                        if sub.type == "type_identifier" and not name:
                            name = _node_text(sub, source)
                        if sub.type in ("struct_type", "interface_type"):
                            kind = sub.type
                    if name and kind:
                        classes.append({
                            "name": name,
                            "file_path": file_path,
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                            "base_classes": [],  # Go has no inheritance
                        })

        for child in node.children:
            visit(child)

    visit(tree_root)
    return classes


def _extract_imports(tree_root, source: bytes, file_path: str) -> list[dict[str, Any]]:
    """Extract import declarations."""
    imports = []

    def visit(node):
        if node.type == "import_declaration":
            for child in node.children:
                if child.type == "import_spec_list":
                    for spec in child.children:
                        if spec.type == "import_spec":
                            path_node = spec.child_by_field_name("path")
                            if path_node:
                                module = _node_text(path_node, source).strip('"')
                                imports.append({
                                    "source_file": file_path,
                                    "imported_module": module,
                                    "imported_names": [],
                                })
                elif child.type == "import_spec":
                    path_node = child.child_by_field_name("path")
                    if path_node:
                        module = _node_text(path_node, source).strip('"')
                        imports.append({
                            "source_file": file_path,
                            "imported_module": module,
                            "imported_names": [],
                        })

        for child in node.children:
            visit(child)

    visit(tree_root)
    return imports


def _extract_env_refs(source_text: str, file_path: str) -> list[dict[str, Any]]:
    """Extract os.Getenv / os.LookupEnv calls."""
    refs = []
    for i, line in enumerate(source_text.splitlines(), 1):
        for m in _ENV_PATTERNS.finditer(line):
            key_name = m.group(1)
            if key_name:
                refs.append({"key_name": key_name, "file_path": file_path, "line": i})
    return refs


def parse_go_file(file_path: str) -> dict[str, Any] | None:
    """
    Parse a .go file with Tree-sitter and return a structure dict
    compatible with the ingestion pipeline.
    """
    try:
        source_text = Path(file_path).read_text(encoding="utf-8", errors="replace")
        source_bytes = source_text.encode("utf-8")
    except OSError as exc:
        logger.debug("Could not read Go file %s: %s", file_path, exc)
        return None

    try:
        parser, language = _get_parser()
        tree = parser.parse(source_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Tree-sitter Go parse failed for %s: %s", file_path, exc)
        return None

    stat = os.stat(file_path)
    return {
        "file_meta": {
            "path": file_path,
            "language": "go",
            "last_modified": stat.st_mtime,
            "hash": "",
            "content": source_text,
        },
        "functions": _extract_functions(tree.root_node, source_bytes, file_path),
        "classes": _extract_classes(tree.root_node, source_bytes, file_path),
        "imports": _extract_imports(tree.root_node, source_bytes, file_path),
        "env_refs": _extract_env_refs(source_text, file_path),
        "variables": [],
    }
