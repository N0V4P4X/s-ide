"""
parser/parsers/js_parser.py
============================
Regex-based semantic extraction for JavaScript, TypeScript, JSX, and TSX.

We deliberately avoid a full JS AST parser (no npm dependency) and instead
use carefully-ordered regexes on comment-stripped source. This is accurate
enough for import/export graph construction and substantially faster to
distribute (zero external deps).

Known limitations vs a real AST:
  - Template literals with backtick-quoted import paths are not captured
  - Type-only imports (TS `import type`) are included as regular imports
  - Complex destructured re-exports may miss some symbol names

Extracted data
--------------
imports     -- ES import (default, named, namespace, side-effect), CJS require, dynamic import
exports     -- named, default, declaration, re-export, re-export-all
definitions -- function declarations, arrow functions, classes, React components
tags        -- framework/library hints: 'react', 'express', 'electron', etc.
"""

from __future__ import annotations
import re
from graph.types import ImportRecord, ExportRecord, Definition

# ── Comment stripping ─────────────────────────────────────────────────────────

def _strip_comments(source: str) -> str:
    """Remove block and line comments, preserving line count."""
    # Block comments /* ... */
    source = re.sub(r"/\*[\s\S]*?\*/", lambda m: "\n" * m.group().count("\n"), source)
    # Line comments //
    source = re.sub(r"//[^\n]*", "", source)
    return source


# ── Import patterns ───────────────────────────────────────────────────────────

# Each pattern is (regex, handler_fn) where handler_fn(match, line) -> ImportRecord | None

def _line_of(source: str, index: int) -> int:
    return source[:index].count("\n") + 1


def _parse_imports(source: str) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    seen: set[tuple] = set()

    def add(rec: ImportRecord) -> None:
        key = (rec.type, rec.source, rec.line)
        if key not in seen:
            seen.add(key)
            records.append(rec)

    # import DefaultName from 'module'
    for m in re.finditer(
        r"^import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]",
        source, re.MULTILINE
    ):
        add(ImportRecord(type="es-default", source=m.group(2),
                         names=[m.group(1)], line=_line_of(source, m.start())))

    # import { X, Y as Z } from 'module'
    for m in re.finditer(
        r"^import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]",
        source, re.MULTILINE
    ):
        raw_names = [n.strip().split(" as ")[0].strip() for n in m.group(1).split(",")]
        names = [n for n in raw_names if n]
        add(ImportRecord(type="es-named", source=m.group(2),
                         names=names, line=_line_of(source, m.start())))

    # import * as NS from 'module'
    for m in re.finditer(
        r"^import\s+\*\s+as\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]",
        source, re.MULTILINE
    ):
        add(ImportRecord(type="es-namespace", source=m.group(2),
                         alias=m.group(1), line=_line_of(source, m.start())))

    # import 'module'  (side-effect)
    for m in re.finditer(
        r"^import\s+['\"]([^'\"]+)['\"]",
        source, re.MULTILINE
    ):
        add(ImportRecord(type="es-side-effect", source=m.group(1),
                         line=_line_of(source, m.start())))

    # const x = require('module')  /  const { a, b } = require('module')
    for m in re.finditer(
        r"(?:const|let|var)\s+([\w{}\s,]+?)\s*=\s*require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
        source, re.MULTILINE
    ):
        add(ImportRecord(type="cjs-require", source=m.group(2),
                         alias=m.group(1).strip(), line=_line_of(source, m.start())))

    # import('module')  dynamic
    for m in re.finditer(
        r"\bimport\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
        source
    ):
        add(ImportRecord(type="dynamic-import", source=m.group(1),
                         line=_line_of(source, m.start())))

    return records


# ── Export patterns ───────────────────────────────────────────────────────────

def _parse_exports(source: str) -> list[ExportRecord]:
    records: list[ExportRecord] = []

    # export { X, Y as Z } from 'module'  — re-export
    for m in re.finditer(
        r"^export\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]",
        source, re.MULTILINE
    ):
        names = [n.strip().split(" as ")[0].strip() for n in m.group(1).split(",") if n.strip()]
        records.append(ExportRecord(type="re-export", names=names,
                                    source=m.group(2), line=_line_of(source, m.start())))

    # export * from 'module'
    for m in re.finditer(
        r"^export\s+\*\s+from\s+['\"]([^'\"]+)['\"]",
        source, re.MULTILINE
    ):
        records.append(ExportRecord(type="re-export-all", source=m.group(1),
                                    line=_line_of(source, m.start())))

    # export { X, Y }  (no from)
    for m in re.finditer(
        r"^export\s+\{([^}]+)\}(?!\s+from)",
        source, re.MULTILINE
    ):
        names = [n.strip().split(" as ")[0].strip() for n in m.group(1).split(",") if n.strip()]
        records.append(ExportRecord(type="named", names=names,
                                    line=_line_of(source, m.start())))

    # export default X
    for m in re.finditer(r"^export\s+default\s+(\w+)", source, re.MULTILINE):
        records.append(ExportRecord(type="default", name=m.group(1),
                                    line=_line_of(source, m.start())))

    # export function/class/const/let/var Name
    for m in re.finditer(
        r"^export\s+(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)",
        source, re.MULTILINE
    ):
        records.append(ExportRecord(type="declaration", name=m.group(1),
                                    line=_line_of(source, m.start())))

    return records


# ── Definition patterns ───────────────────────────────────────────────────────

def _parse_definitions(source: str) -> list[Definition]:
    defs: list[Definition] = []
    seen: set[str] = set()

    def add(d: Definition) -> None:
        key = f"{d.kind}:{d.name}:{d.line}"
        if key not in seen:
            seen.add(key)
            defs.append(d)

    # function declarations (including async, including exported)
    for m in re.finditer(
        r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(",
        source, re.MULTILINE
    ):
        add(Definition(name=m.group(1), kind="function",
                       line=_line_of(source, m.start()),
                       is_async="async" in m.group(0)))

    # const/let Name = (...) => ...   arrow functions
    for m in re.finditer(
        r"^(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>",
        source, re.MULTILINE
    ):
        add(Definition(name=m.group(1), kind="arrow-function",
                       line=_line_of(source, m.start()),
                       is_async="async" in m.group(0)))

    # class declarations
    for m in re.finditer(
        r"^(?:export\s+)?class\s+(\w+)",
        source, re.MULTILINE
    ):
        add(Definition(name=m.group(1), kind="class",
                       line=_line_of(source, m.start())))

    # React components (capital-named const/function — upgrade kind)
    for m in re.finditer(
        r"^(?:export\s+)?(?:const|function)\s+([A-Z]\w+)",
        source, re.MULTILINE
    ):
        name = m.group(1)
        existing = next((d for d in defs if d.name == name), None)
        if existing:
            existing.kind = "component"
        else:
            add(Definition(name=name, kind="component",
                           line=_line_of(source, m.start())))

    defs.sort(key=lambda d: d.line or 0)
    return defs


# ── Framework detection ───────────────────────────────────────────────────────

_FRAMEWORK_PATTERNS: list[tuple[str, str]] = [
    (r"from\s+['\"]react['\"]",               "react"),
    (r"from\s+['\"]vue['\"]",                 "vue"),
    (r"from\s+['\"]svelte['\"]",              "svelte"),
    (r"from\s+['\"]express['\"]",             "express"),
    (r"from\s+['\"]fastify['\"]",             "fastify"),
    (r"from\s+['\"]next['\"]",                "next"),
    (r"from\s+['\"]electron['\"]",            "electron"),
    (r"WebSocket|ws\.on|socket\.io",          "websocket"),
    (r"fetch\(|axios\.|\.get\(|\.post\(",     "http-client"),
]

def _detect_frameworks(source: str, file_path: str = "") -> list[str]:
    tags = []
    for pattern, tag in _FRAMEWORK_PATTERNS:
        if re.search(pattern, source, re.IGNORECASE):
            tags.append(tag)
    if "/pages/" in file_path or "/app/" in file_path:
        if "next" not in tags:
            tags.append("next")
    return tags


# ── Public entry point ────────────────────────────────────────────────────────

def parse_javascript(source: str, file_path: str = "") -> dict:
    """
    Parse a JS/TS/JSX/TSX source file.
    Returns dict with: imports, exports, definitions, tags, errors.
    """
    stripped = _strip_comments(source)
    return {
        "imports":     _parse_imports(stripped),
        "exports":     _parse_exports(stripped),
        "definitions": _parse_definitions(stripped),
        "tags":        _detect_frameworks(source, file_path),
        "errors":      [],
    }
