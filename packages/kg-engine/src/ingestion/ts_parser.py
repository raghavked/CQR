"""
TypeScript / TSX Tree-sitter parser for KG ingestion.

Extracts the same node types as the Python parser:
  Function, Class, Import, Variable, EnvRef

Handles .ts and .tsx files.
TypeScript is a superset of JavaScript — the TS grammar extends the JS grammar,
so most extraction logic is identical to js_parser.py with TS-specific additions
(type annotations, interfaces, type aliases, decorators).
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TS_LANGUAGE = None
_TSX_LANGUAGE = None
_TS_PARSER = None
_TSX_PARSER = None

_ENV_PATTERNS = re.compile(
    r"process\.env\.([A-Z_][A-Z0-9_]*)|"
    r"process\.env\[[\'\"]([A-Z_][A-Z0-9_]*)[\'\"]",
    re.MULTILINE,
)


def _get_parser(tsx: bool = False):
    """Lazy-initialise the Tree-sitter TS or TSX parser."""
    global _TS_LANGUAGE, _TSX_LANGUAGE, _TS_PARSER, _TSX_PARSER
    import tree_sitter_typescript as tsts
    from tree_sitter import Language, Parser

    if tsx:
        if _TSX_PARSER is None:
            _TSX_LANGUAGE = Language(tsts.language_tsx())
            _TSX_PARSER = Parser(_TSX_LANGUAGE)
        return _TSX_PARSER, _TSX_LANGUAGE
    else:
        if _TS_PARSER is None:
            _TS_LANGUAGE = Language(tsts.language_typescript())
            _TS_PARSER = Parser(_TS_LANGUAGE)
        return _TS_PARSER, _TS_LANGUAGE


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_jsdoc(node, source: bytes) -> str:
    prev = node.prev_sibling
    if prev and prev.type == "comment":
        text = _node_text(prev, source).strip()
        if text.startswith("/**"):
            return text
    return ""


def _extract_functions(tree_root, source: bytes, file_path: str) -> list[dict[str, Any]]:
    """Extract function declarations, arrow functions, method definitions, and async functions."""
    functions = []

    def visit(node):
        if node.type in (
            "function_declaration",
            "function",
            "arrow_function",
            "method_definition",
            "generator_function_declaration",
            "generator_function",
            # TS-specific
            "function_signature",
            "method_signature",
            "abstract_method_signature",
        ):
            name = ""
            for child in node.children:
                if child.type in ("identifier", "property_identifier"):
                    name = _node_text(child, source)
                    break

            if not name and node.parent and node.parent.type == "variable_declarator":
                for child in node.parent.children:
                    if child.type == "identifier":
                        name = _node_text(child, source)
                        break

            if not name:
                name = "<anonymous>"

            params = ""
            for child in node.children:
                if child.type in ("formal_parameters", "parameters"):
                    params = _node_text(child, source)
                    break

            # Extract return type annotation if present
            return_type = ""
            for child in node.children:
                if child.type == "type_annotation":
                    return_type = _node_text(child, source)
                    break

            signature = f"function {name}{params}{return_type}"
            functions.append({
                "name": name,
                "file_path": file_path,
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "signature": signature,
                "docstring": _get_jsdoc(node, source),
            })

        for child in node.children:
            visit(child)

    visit(tree_root)
    return functions


def _extract_classes(tree_root, source: bytes, file_path: str) -> list[dict[str, Any]]:
    """Extract class declarations and interface declarations."""
    classes = []

    def visit(node):
        if node.type in ("class_declaration", "interface_declaration", "abstract_class_declaration"):
            name = ""
            base_classes = []
            for child in node.children:
                if child.type == "type_identifier" and not name:
                    name = _node_text(child, source)
                if child.type == "class_heritage":
                    for sub in child.children:
                        if sub.type in ("identifier", "type_identifier"):
                            base_classes.append(_node_text(sub, source))
                if child.type == "extends_clause":
                    for sub in child.children:
                        if sub.type in ("identifier", "type_identifier"):
                            base_classes.append(_node_text(sub, source))
                if child.type == "implements_clause":
                    for sub in child.children:
                        if sub.type in ("identifier", "type_identifier"):
                            base_classes.append(_node_text(sub, source))

            if name:
                classes.append({
                    "name": name,
                    "file_path": file_path,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "base_classes": base_classes,
                })

        for child in node.children:
            visit(child)

    visit(tree_root)
    return classes


def _extract_imports(tree_root, source: bytes, file_path: str) -> list[dict[str, Any]]:
    """Extract ES6 import statements and require() calls."""
    imports = []

    def visit(node):
        if node.type == "import_statement":
            module = ""
            names = []
            for child in node.children:
                if child.type == "string":
                    module = _node_text(child, source).strip("'\"")
                if child.type in ("import_clause", "named_imports"):
                    for sub in child.children:
                        if sub.type in ("identifier", "type_identifier"):
                            names.append(_node_text(sub, source))
                        if sub.type == "import_specifier":
                            for s2 in sub.children:
                                if s2.type in ("identifier", "type_identifier"):
                                    names.append(_node_text(s2, source))
                                    break
            if module:
                imports.append({
                    "source_file": file_path,
                    "imported_module": module,
                    "imported_names": names,
                })

        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn and _node_text(fn, source) == "require":
                args = node.child_by_field_name("arguments")
                if args:
                    for child in args.children:
                        if child.type == "string":
                            module = _node_text(child, source).strip("'\"")
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
    refs = []
    for i, line in enumerate(source_text.splitlines(), 1):
        for m in _ENV_PATTERNS.finditer(line):
            key_name = m.group(1) or m.group(2)
            if key_name:
                refs.append({"key_name": key_name, "file_path": file_path, "line": i})
    return refs


def parse_ts_file(file_path: str) -> dict[str, Any] | None:
    """
    Parse a .ts / .tsx file with Tree-sitter and return a structure dict
    compatible with the ingestion pipeline.
    """
    try:
        source_text = Path(file_path).read_text(encoding="utf-8", errors="replace")
        source_bytes = source_text.encode("utf-8")
    except OSError as exc:
        logger.debug("Could not read TS file %s: %s", file_path, exc)
        return None

    tsx = file_path.endswith(".tsx")
    try:
        parser, language = _get_parser(tsx=tsx)
        tree = parser.parse(source_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Tree-sitter TS parse failed for %s: %s", file_path, exc)
        return None

    stat = os.stat(file_path)
    return {
        "file_meta": {
            "path": file_path,
            "language": "tsx" if tsx else "typescript",
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
