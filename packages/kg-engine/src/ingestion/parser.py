"""
Tree-sitter based AST parser for Python, JavaScript/JSX, TypeScript/TSX, and Go.

Routing:
  .py          → _parse_python_treesitter (Tree-sitter) or _parse_python_fallback (regex)
  .js / .jsx   → js_parser.parse_js_file (Tree-sitter)
  .ts / .tsx   → ts_parser.parse_ts_file (Tree-sitter)
  .go          → go_parser.parse_go_file (Tree-sitter)

The regex fallback is now Python-only. JS/TS/Go use dedicated Tree-sitter parsers
that extract Functions, Classes, Imports, and EnvRefs with full AST accuracy.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
}


def detect_language(file_path: str) -> str | None:
    """Return the language identifier for a file based on its extension."""
    ext = Path(file_path).suffix.lower()
    return _LANGUAGE_MAP.get(ext)


# ---------------------------------------------------------------------------
# Python Tree-sitter parser (lazy-loaded)
# ---------------------------------------------------------------------------

_PY_PARSER = None


def _get_python_parser() -> Any | None:
    """Lazy-load and cache the Tree-sitter Python parser."""
    global _PY_PARSER
    if _PY_PARSER is None:
        try:
            import tree_sitter_python as tspython
            from tree_sitter import Language, Parser

            lang = Language(tspython.language())
            _PY_PARSER = Parser(lang)
        except ImportError as exc:
            logger.warning("tree-sitter-python not available: %s — using regex fallback", exc)
    return _PY_PARSER


# ---------------------------------------------------------------------------
# Python regex fallback (used only when Tree-sitter is unavailable)
# ---------------------------------------------------------------------------

_PY_FUNC_RE = re.compile(
    r"^(?P<indent>\s*)(?:async\s+)?def\s+(?P<name>\w+)\s*\((?P<args>[^)]*)\)\s*(?:->.*?)?:",
    re.MULTILINE,
)
_PY_CLASS_RE = re.compile(
    r"^(?P<indent>\s*)class\s+(?P<name>\w+)\s*(?:\((?P<bases>[^)]*)\))?:",
    re.MULTILINE,
)
_PY_IMPORT_RE = re.compile(
    r"^(?:from\s+(?P<module>[\w.]+)\s+import\s+(?P<names>.+)|import\s+(?P<imod>[\w., ]+))",
    re.MULTILINE,
)
_PY_ENVREF_RE = re.compile(
    r'os\.environ(?:\.get)?\s*\[\s*["\'](?P<key>[^"\']+)["\']'
    r'|os\.environ\.get\s*\(\s*["\'](?P<key2>[^"\']+)["\']',
    re.MULTILINE,
)


def _parse_python_fallback(source: str, file_path: str) -> dict[str, Any]:
    """Extract code structure from Python source using regex (fallback)."""
    functions = []
    classes = []
    imports = []
    env_refs = []

    for match in _PY_FUNC_RE.finditer(source):
        start_line = source[: match.start()].count("\n") + 1
        functions.append(
            {
                "name": match.group("name"),
                "file_path": file_path,
                "start_line": start_line,
                "end_line": start_line,
                "signature": match.group(0).strip().rstrip(":"),
                "docstring": "",
            }
        )

    for match in _PY_CLASS_RE.finditer(source):
        start_line = source[: match.start()].count("\n") + 1
        bases = [b.strip() for b in (match.group("bases") or "").split(",") if b.strip()]
        classes.append(
            {
                "name": match.group("name"),
                "file_path": file_path,
                "start_line": start_line,
                "end_line": start_line,
                "base_classes": bases,
            }
        )

    for match in _PY_IMPORT_RE.finditer(source):
        module = match.group("module") or match.group("imod") or ""
        names_raw = match.group("names") or ""
        names = [n.strip() for n in names_raw.split(",") if n.strip()]
        imports.append(
            {
                "source_file": file_path,
                "imported_module": module.strip(),
                "imported_names": names,
            }
        )

    for match in _PY_ENVREF_RE.finditer(source):
        key = match.group("key") or match.group("key2")
        line = source[: match.start()].count("\n") + 1
        env_refs.append({"key_name": key, "file_path": file_path, "line": line})

    return {
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "env_refs": env_refs,
        "variables": [],
    }


# ---------------------------------------------------------------------------
# Python Tree-sitter AST parser
# ---------------------------------------------------------------------------


def _extract_docstring(node: Any, source_bytes: bytes) -> str:
    """Extract docstring from the first expression statement in a function/class body."""
    try:
        for child in node.children:
            if child.type == "block":
                for stmt in child.children:
                    if stmt.type == "expression_statement":
                        for sub in stmt.children:
                            if sub.type == "string":
                                raw = source_bytes[sub.start_byte:sub.end_byte].decode(
                                    "utf-8", errors="replace"
                                )
                                return raw.strip('"\' \n\t').strip('"""').strip("'''")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _parse_python_treesitter(source: str, file_path: str, parser: Any) -> dict[str, Any]:
    """Extract code structure from Python source using Tree-sitter."""
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    root = tree.root_node

    functions: list[dict] = []
    classes: list[dict] = []
    imports: list[dict] = []
    env_refs: list[dict] = []

    def walk(node: Any) -> None:
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            name = (
                source_bytes[name_node.start_byte:name_node.end_byte].decode()
                if name_node
                else ""
            )
            params_node = node.child_by_field_name("parameters")
            signature = (
                f"def {name}"
                f"{source_bytes[params_node.start_byte:params_node.end_byte].decode() if params_node else '()'}"
            )
            functions.append(
                {
                    "name": name,
                    "file_path": file_path,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "signature": signature,
                    "docstring": _extract_docstring(node, source_bytes),
                }
            )
        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            name = (
                source_bytes[name_node.start_byte:name_node.end_byte].decode()
                if name_node
                else ""
            )
            bases: list[str] = []
            args_node = node.child_by_field_name("superclasses")
            if args_node:
                for child in args_node.children:
                    if child.type == "identifier":
                        bases.append(
                            source_bytes[child.start_byte:child.end_byte].decode()
                        )
            classes.append(
                {
                    "name": name,
                    "file_path": file_path,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "base_classes": bases,
                }
            )
        elif node.type in ("import_statement", "import_from_statement"):
            module = ""
            names: list[str] = []
            if node.type == "import_from_statement":
                mod_node = node.child_by_field_name("module_name")
                module = (
                    source_bytes[mod_node.start_byte:mod_node.end_byte].decode()
                    if mod_node
                    else ""
                )
                for child in node.children:
                    if child.type == "dotted_name" and child != mod_node:
                        names.append(
                            source_bytes[child.start_byte:child.end_byte].decode()
                        )
                    elif child.type == "aliased_import":
                        names.append(
                            source_bytes[child.start_byte:child.end_byte].decode()
                        )
            else:
                for child in node.children:
                    if child.type == "dotted_name":
                        module = source_bytes[child.start_byte:child.end_byte].decode()
            imports.append(
                {
                    "source_file": file_path,
                    "imported_module": module,
                    "imported_names": names,
                }
            )
        elif node.type == "call":
            raw = source_bytes[node.start_byte:node.end_byte].decode()
            for match in _PY_ENVREF_RE.finditer(raw):
                key = match.group("key") or match.group("key2")
                line = node.start_point[0] + 1
                env_refs.append({"key_name": key, "file_path": file_path, "line": line})

        for child in node.children:
            walk(child)

    walk(root)
    return {
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "env_refs": env_refs,
        "variables": [],
    }


# ---------------------------------------------------------------------------
# Public parse function — routes to the correct language parser
# ---------------------------------------------------------------------------


def parse_file(file_path: str) -> dict[str, Any] | None:
    """
    Parse a source file and return extracted code structure.
    Returns None if the file language is not supported.

    Routing:
      Python       → Tree-sitter (with regex fallback)
      JS / JSX     → js_parser.parse_js_file (Tree-sitter)
      TS / TSX     → ts_parser.parse_ts_file (Tree-sitter)
      Go           → go_parser.parse_go_file (Tree-sitter)
    """
    language = detect_language(file_path)
    if not language:
        return None

    # --- JavaScript / JSX ---
    if language == "javascript":
        from .js_parser import parse_js_file
        return parse_js_file(file_path)

    # --- TypeScript / TSX ---
    if language in ("typescript", "tsx"):
        from .ts_parser import parse_ts_file
        return parse_ts_file(file_path)

    # --- Go ---
    if language == "go":
        from .go_parser import parse_go_file
        return parse_go_file(file_path)

    # --- Python (Tree-sitter with regex fallback) ---
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            source = fh.read()
    except OSError as exc:
        logger.error("Cannot read file %s: %s", file_path, exc)
        return None

    file_hash = hashlib.sha256(source.encode()).hexdigest()
    mtime = os.path.getmtime(file_path)

    py_parser = _get_python_parser()
    if py_parser:
        structure = _parse_python_treesitter(source, file_path, py_parser)
    else:
        structure = _parse_python_fallback(source, file_path)

    structure["file_meta"] = {
        "path": file_path,
        "language": language,
        "hash": file_hash,
        "last_modified": mtime,
        "content": source,
    }
    return structure
