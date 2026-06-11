"""
Tree-sitter based AST parser for Python, JavaScript/TypeScript, and Go.
Extracts functions, classes, imports, and env references from source files.
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
    ".tsx": "typescript",
    ".go": "go",
}


def detect_language(file_path: str) -> str | None:
    """Return the language identifier for a file based on its extension."""
    ext = Path(file_path).suffix.lower()
    return _LANGUAGE_MAP.get(ext)


# ---------------------------------------------------------------------------
# Tree-sitter parser loader
# ---------------------------------------------------------------------------

_parsers: dict[str, Any] = {}


def _get_parser(language: str) -> Any | None:
    """Lazy-load and cache a Tree-sitter parser for the given language."""
    if language in _parsers:
        return _parsers[language]
    try:
        import tree_sitter_python as tspython
        import tree_sitter_javascript as tsjavascript
        from tree_sitter import Language, Parser

        lang_map = {
            "python": tspython.language(),
            "javascript": tsjavascript.language(),
            "typescript": tsjavascript.language(),  # TODO(AMBIGUITY): use ts-specific grammar when available
        }
        if language not in lang_map:
            logger.warning("No Tree-sitter grammar available for language: %s", language)
            return None
        lang = Language(lang_map[language])
        parser = Parser(lang)
        _parsers[language] = parser
        return parser
    except ImportError as exc:
        logger.warning("Tree-sitter import failed (%s) — falling back to regex parser", exc)
        return None


# ---------------------------------------------------------------------------
# Python regex fallback parser (used when Tree-sitter is unavailable)
# ---------------------------------------------------------------------------

_PY_FUNC_RE = re.compile(r"^(?P<indent>\s*)(?:async\s+)?def\s+(?P<name>\w+)\s*\((?P<args>[^)]*)\)\s*(?:->.*?)?:", re.MULTILINE)
_PY_CLASS_RE = re.compile(r"^(?P<indent>\s*)class\s+(?P<name>\w+)\s*(?:\((?P<bases>[^)]*)\))?:", re.MULTILINE)
_PY_IMPORT_RE = re.compile(r"^(?:from\s+(?P<module>[\w.]+)\s+import\s+(?P<names>.+)|import\s+(?P<imod>[\w., ]+))", re.MULTILINE)
_PY_ENVREF_RE = re.compile(r'os\.environ(?:\.get)?\s*\[\s*["\'](?P<key>[^"\']+)["\']|os\.environ\.get\s*\(\s*["\'](?P<key2>[^"\']+)["\']', re.MULTILINE)


def _parse_python_fallback(source: str, file_path: str) -> dict[str, Any]:
    """Extract code structure from Python source using regex (fallback)."""
    lines = source.splitlines()
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
                "end_line": start_line,  # TODO(AMBIGUITY): end line requires full AST walk
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
# Tree-sitter AST parser (Python)
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
                                raw = source_bytes[sub.start_byte:sub.end_byte].decode("utf-8", errors="replace")
                                return raw.strip('"\' \n\t').strip('"""').strip("'''")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _parse_python_treesitter(source: str, file_path: str, parser: Any) -> dict[str, Any]:
    """Extract code structure from Python source using Tree-sitter."""
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    root = tree.root_node

    functions = []
    classes = []
    imports = []
    env_refs = []

    def walk(node: Any) -> None:
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            name = source_bytes[name_node.start_byte:name_node.end_byte].decode() if name_node else ""
            params_node = node.child_by_field_name("parameters")
            signature = f"def {name}{source_bytes[params_node.start_byte:params_node.end_byte].decode() if params_node else '()'}"
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
            name = source_bytes[name_node.start_byte:name_node.end_byte].decode() if name_node else ""
            bases = []
            args_node = node.child_by_field_name("superclasses")
            if args_node:
                for child in args_node.children:
                    if child.type == "identifier":
                        bases.append(source_bytes[child.start_byte:child.end_byte].decode())
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
            raw = source_bytes[node.start_byte:node.end_byte].decode()
            module = ""
            names: list[str] = []
            if node.type == "import_from_statement":
                mod_node = node.child_by_field_name("module_name")
                module = source_bytes[mod_node.start_byte:mod_node.end_byte].decode() if mod_node else ""
                for child in node.children:
                    if child.type == "dotted_name" and child != mod_node:
                        names.append(source_bytes[child.start_byte:child.end_byte].decode())
                    elif child.type == "aliased_import":
                        names.append(source_bytes[child.start_byte:child.end_byte].decode())
            else:
                for child in node.children:
                    if child.type == "dotted_name":
                        module = source_bytes[child.start_byte:child.end_byte].decode()
            imports.append(
                {"source_file": file_path, "imported_module": module, "imported_names": names}
            )
        elif node.type == "call":
            # Detect os.environ['KEY'] and os.environ.get('KEY')
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
# Public parse function
# ---------------------------------------------------------------------------


def parse_file(file_path: str) -> dict[str, Any] | None:
    """
    Parse a source file and return extracted code structure.
    Returns None if the file language is not supported.
    """
    language = detect_language(file_path)
    if not language:
        return None

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            source = fh.read()
    except OSError as exc:
        logger.error("Cannot read file %s: %s", file_path, exc)
        return None

    file_hash = hashlib.sha256(source.encode()).hexdigest()
    mtime = os.path.getmtime(file_path)

    parser = _get_parser(language)
    if parser and language == "python":
        structure = _parse_python_treesitter(source, file_path, parser)
    else:
        # Fallback for JS/TS/Go — regex is limited but functional for MVP
        structure = _parse_python_fallback(source, file_path)

    structure["file_meta"] = {
        "path": file_path,
        "language": language,
        "hash": file_hash,
        "last_modified": mtime,
    }
    return structure
