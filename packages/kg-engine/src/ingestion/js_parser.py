"""
JavaScript / JSX Tree-sitter parser for KG ingestion.

Extracts the same node types as the Python parser:
  Function, Class, Import, Variable, EnvRef

Handles .js and .jsx files.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Lazy-loaded language + parser (initialised on first use)
_JS_LANGUAGE = None
_JS_PARSER = None

# Patterns for EnvRef detection
_ENV_PATTERNS = re.compile(
    r"process\.env\.([A-Z_][A-Z0-9_]*)|"
    r"process\.env\[[\'\"]([A-Z_][A-Z0-9_]*)[\'\"]",
    re.MULTILINE,
)

# Patterns for hardcoded credential detection (passed to Variable nodes)
_CRED_PATTERNS = re.compile(
    r"(?:api[_-]?key|secret|password|token|auth)['\"]?\s*[:=]\s*['\"][A-Za-z0-9+/=_\-]{16,}['\"]",
    re.IGNORECASE,
)


def _get_parser():
    """Lazy-initialise the Tree-sitter JS parser."""
    global _JS_LANGUAGE, _JS_PARSER
    if _JS_PARSER is None:
        import tree_sitter_javascript as tsjs
        from tree_sitter import Language, Parser

        _JS_LANGUAGE = Language(tsjs.language())
        _JS_PARSER = Parser(_JS_LANGUAGE)
    return _JS_PARSER, _JS_LANGUAGE


def _node_text(node, source: bytes) -> str:
    """Extract the source text for a Tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_jsdoc(node, source: bytes) -> str:
    """Extract JSDoc comment immediately preceding a node, if any."""
    # Walk backwards through siblings to find a comment
    prev = node.prev_sibling
    if prev and prev.type == "comment":
        text = _node_text(prev, source).strip()
        if text.startswith("/**"):
            return text
    return ""


def _extract_functions(tree_root, source: bytes, file_path: str) -> list[dict[str, Any]]:
    """Extract all function declarations, arrow functions, and method definitions."""
    functions = []

    def visit(node):
        if node.type in (
            "function_declaration",
            "function",
            "arrow_function",
            "method_definition",
            "generator_function_declaration",
            "generator_function",
        ):
            # Get name
            name = ""
            for child in node.children:
                if child.type == "identifier":
                    name = _node_text(child, source)
                    break
                if child.type == "property_identifier":
                    name = _node_text(child, source)
                    break

            # For arrow functions assigned to variables, name comes from parent
            if not name and node.parent and node.parent.type == "variable_declarator":
                for child in node.parent.children:
                    if child.type == "identifier":
                        name = _node_text(child, source)
                        break

            if not name:
                name = "<anonymous>"

            # Get parameters
            params = ""
            for child in node.children:
                if child.type in ("formal_parameters", "parameters"):
                    params = _node_text(child, source)
                    break

            functions.append({
                "name": name,
                "file_path": file_path,
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "signature": f"function {name}{params}",
                "docstring": _get_jsdoc(node, source),
            })

        for child in node.children:
            visit(child)

    visit(tree_root)
    return functions


def _extract_classes(tree_root, source: bytes, file_path: str) -> list[dict[str, Any]]:
    """Extract all class declarations."""
    classes = []

    def visit(node):
        if node.type == "class_declaration":
            name = ""
            base_classes = []
            for child in node.children:
                if child.type == "identifier" and not name:
                    name = _node_text(child, source)
                if child.type == "class_heritage":
                    for sub in child.children:
                        if sub.type == "identifier":
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
        # ES6: import X from 'module'
        if node.type == "import_statement":
            module = ""
            names = []
            for child in node.children:
                if child.type == "string":
                    module = _node_text(child, source).strip("'\"")
                if child.type in ("import_clause", "named_imports"):
                    for sub in child.children:
                        if sub.type == "identifier":
                            names.append(_node_text(sub, source))
                        if sub.type == "import_specifier":
                            for s2 in sub.children:
                                if s2.type == "identifier":
                                    names.append(_node_text(s2, source))
                                    break
            if module:
                imports.append({
                    "source_file": file_path,
                    "imported_module": module,
                    "imported_names": names,
                })

        # CommonJS: const X = require('module')
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
    """Extract process.env.KEY references using regex (simpler than AST for this case)."""
    refs = []
    for i, line in enumerate(source_text.splitlines(), 1):
        for m in _ENV_PATTERNS.finditer(line):
            key_name = m.group(1) or m.group(2)
            if key_name:
                refs.append({
                    "key_name": key_name,
                    "file_path": file_path,
                    "line": i,
                })
    return refs


def parse_js_file(file_path: str) -> dict[str, Any] | None:
    """
    Parse a .js / .jsx file with Tree-sitter and return a structure dict
    compatible with the ingestion pipeline.
    """
    try:
        source_text = Path(file_path).read_text(encoding="utf-8", errors="replace")
        source_bytes = source_text.encode("utf-8")
    except OSError as exc:
        logger.debug("Could not read JS file %s: %s", file_path, exc)
        return None

    try:
        parser, language = _get_parser()
        tree = parser.parse(source_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Tree-sitter JS parse failed for %s: %s", file_path, exc)
        return None

    stat = os.stat(file_path)
    return {
        "file_meta": {
            "path": file_path,
            "language": "javascript",
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
