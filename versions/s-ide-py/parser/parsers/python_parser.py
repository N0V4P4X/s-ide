"""
parser/parsers/python_parser.py
================================
Extracts semantic structure from Python source files using the stdlib
ast module. Single-pass: all data (imports, definitions, exports, tags,
data-flow) collected in one ast.walk call.

Extracted data
--------------
imports     -- import X, from X import Y, from . import Z
exports     -- __all__ list + all top-level public definitions
definitions -- functions, classes (with args, return type, calls, raises,
               cyclomatic complexity)
tags        -- framework/role tags: flask, fastapi, django, entrypoint…
"""

from __future__ import annotations
import ast
import re
from typing import Any


def _annotation_str(node: ast.expr | None) -> str:
    """Convert an annotation AST node to a readable string."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _complexity(body: list[ast.stmt]) -> int:
    """
    Rough cyclomatic complexity: 1 + number of branching statements.
    Counts: if/elif, for, while, except, with, assert, comprehensions.
    """
    count = 1
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.ExceptHandler,
                              ast.With, ast.Assert,
                              ast.ListComp, ast.SetComp, ast.DictComp,
                              ast.GeneratorExp)):
            count += 1
    return count


def _collect_calls(body: list[ast.stmt]) -> list[str]:
    """Collect all function/method call names within a function body."""
    calls = []
    seen  = set()
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Call):
            try:
                name = ast.unparse(node.func)
                # Keep it short: strip long attribute chains past 2 levels
                parts = name.split(".")
                short = ".".join(parts[-2:]) if len(parts) > 2 else name
                if short and short not in seen:
                    seen.add(short)
                    calls.append(short)
            except Exception:
                pass
    return calls[:20]   # cap to avoid noise


def _collect_raises(body: list[ast.stmt]) -> list[str]:
    """Collect exception types raised in a function body."""
    raises = []
    seen   = set()
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Raise) and node.exc is not None:
            try:
                name = ast.unparse(node.exc)
                # Strip constructor call: ValueError("msg") → ValueError
                name = re.sub(r'\(.*', '', name).strip()
                if name and name not in seen:
                    seen.add(name)
                    raises.append(name)
            except Exception:
                pass
    return raises


class _SinglePassVisitor(ast.NodeVisitor):
    """
    One pass over the AST collecting everything we need.
    Avoids three separate ast.walk calls on large files.
    """

    def __init__(self):
        self.imports:     list[dict] = []
        self.definitions: list[dict] = []
        self.tags:        set[str]   = set()
        self._all_names:  list[str] | None = None
        self._top_level_names: list[str]   = []   # public names at module level
        self._depth = 0   # nesting depth (0 = module level)

    # ── Imports ──────────────────────────────────────────────────────────────

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append({
                "type":   "import",
                "source": alias.name,
                "alias":  alias.asname,
                "line":   node.lineno,
            })
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        dots   = "." * (node.level or 0)
        source = f"{dots}{module}" if module else dots
        names  = [a.name for a in node.names]
        rec = {
            "source": source,
            "line":   node.lineno,
        }
        if names == ["*"]:
            rec["type"] = "from-import-all"
        else:
            rec["type"]  = "from-import"
            rec["names"] = names
        self.imports.append(rec)
        self.generic_visit(node)

    # ── Definitions ───────────────────────────────────────────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._visit_func(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._visit_func(node, is_async=True)

    def _visit_func(self, node, is_async: bool):
        name = node.name
        decorators = []
        for d in node.decorator_list:
            try:
                decorators.append(ast.unparse(d))
            except Exception:
                pass

        # Detect framework tags from decorators
        for dec in decorators:
            if "route" in dec or "get" in dec or "post" in dec:
                self.tags.add("flask")
            if "app." in dec:
                self.tags.add("flask")

        # Args: [(name, annotation)]
        args = []
        for arg in node.args.args:
            args.append((arg.arg, _annotation_str(arg.annotation)))
        for arg in node.args.posonlyargs:
            args.append((arg.arg, _annotation_str(arg.annotation)))
        if node.args.vararg:
            args.append(("*" + node.args.vararg.arg,
                          _annotation_str(node.args.vararg.annotation)))
        for arg in node.args.kwonlyargs:
            args.append((arg.arg, _annotation_str(arg.annotation)))
        if node.args.kwarg:
            args.append(("**" + node.args.kwarg.arg,
                          _annotation_str(node.args.kwarg.annotation)))

        return_type = _annotation_str(node.returns)
        calls    = _collect_calls(node.body)
        raises   = _collect_raises(node.body)
        compl    = _complexity(node.body)

        kind = "dunder" if (name.startswith("__") and name.endswith("__")) \
               else ("method" if self._depth > 0 else "function")

        d = {
            "name":       name,
            "kind":       kind,
            "line":       node.lineno,
            "end_line":   getattr(node, "end_lineno", None),
            "indent":     node.col_offset,
            "is_async":   is_async,
            "decorators": decorators,
            "bases":      [],
            "args":       args,
            "return_type":return_type,
            "calls":      calls,
            "raises":     raises,
            "complexity": compl,
        }
        self.definitions.append(d)

        if self._depth == 0 and not name.startswith("_"):
            self._top_level_names.append(name)

        # Visit body at increased depth
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_ClassDef(self, node: ast.ClassDef):
        bases = []
        for b in node.bases:
            try:
                bases.append(ast.unparse(b))
            except Exception:
                pass

        decorators = []
        for d in node.decorator_list:
            try:
                decorators.append(ast.unparse(d))
            except Exception:
                pass

        self.definitions.append({
            "name":       node.name,
            "kind":       "class",
            "line":       node.lineno,
            "end_line":   getattr(node, "end_lineno", None),
            "indent":     node.col_offset,
            "is_async":   False,
            "decorators": decorators,
            "bases":      bases,
            "args":       [],
            "return_type":"",
            "calls":      [],
            "raises":     [],
            "complexity": 0,
        })

        if self._depth == 0 and not node.name.startswith("_"):
            self._top_level_names.append(node.name)

        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    # ── __all__ ───────────────────────────────────────────────────────────────

    def visit_Assign(self, node: ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    self._all_names = [
                        elt.s if isinstance(elt, ast.Constant) and isinstance(elt.s, str)
                        else ast.unparse(elt)
                        for elt in node.value.elts
                    ]
        self.generic_visit(node)

    # ── Tags ──────────────────────────────────────────────────────────────────

    def visit_Call(self, node: ast.Call):
        """Detect framework usage from call patterns."""
        try:
            fn = ast.unparse(node.func)
            if "Flask(" in fn or fn == "Flask":
                self.tags.add("flask")
            if "FastAPI(" in fn or fn == "FastAPI":
                self.tags.add("fastapi")
            if "Django" in fn or "django" in fn:
                self.tags.add("django")
            if fn in ("pytest.fixture", "unittest.main"):
                self.tags.add("test")
        except Exception:
            pass
        self.generic_visit(node)


# ── Entry-point detection ─────────────────────────────────────────────────────

_ENTRYPOINT_PATTERNS = [
    r'if\s+__name__\s*==\s*["\']__main__["\']',
    r'\.run\s*\(',
    r'uvicorn\.run\s*\(',
    r'app\.run\s*\(',
]
_ENTRYPOINT_RE = re.compile("|".join(_ENTRYPOINT_PATTERNS))

_FRAMEWORK_SOURCE_PATTERNS = {
    "flask":    re.compile(r"\bflask\b", re.I),
    "fastapi":  re.compile(r"\bfastapi\b", re.I),
    "django":   re.compile(r"\bdjango\b", re.I),
    "pytest":   re.compile(r"\bpytest\b|\bunittest\b", re.I),
    "sqlalchemy":re.compile(r"\bsqlalchemy\b", re.I),
    "pydantic": re.compile(r"\bpydantic\b", re.I),
    "asyncio":  re.compile(r"\basyncio\b", re.I),
}


def _detect_tags_from_source(source: str, visitor: _SinglePassVisitor) -> list[str]:
    tags = set(visitor.tags)
    for tag, pattern in _FRAMEWORK_SOURCE_PATTERNS.items():
        if pattern.search(source):
            tags.add(tag)
    if _ENTRYPOINT_RE.search(source):
        tags.add("entrypoint")
    return sorted(tags)


# ── Fallback (regex) ──────────────────────────────────────────────────────────

def _regex_parse(source: str) -> dict:
    """Minimal regex extraction when ast.parse fails."""
    imports, defs = [], []
    for m in re.finditer(r'^import\s+([\w.,\s]+)', source, re.M):
        for name in m.group(1).split(","):
            imports.append({"type": "import", "source": name.strip()})
    for m in re.finditer(r'^from\s+([\w.]+)\s+import', source, re.M):
        imports.append({"type": "from-import", "source": m.group(1)})
    for m in re.finditer(r'^(async\s+)?def\s+(\w+)\s*\(', source, re.M):
        defs.append({"name": m.group(2), "kind": "function",
                     "line": source[:m.start()].count("\n") + 1,
                     "is_async": bool(m.group(1)), "indent": 0,
                     "decorators": [], "bases": [], "args": [],
                     "return_type": "", "calls": [], "raises": [], "complexity": 1})
    for m in re.finditer(r'^class\s+(\w+)', source, re.M):
        defs.append({"name": m.group(1), "kind": "class",
                     "line": source[:m.start()].count("\n") + 1,
                     "is_async": False, "indent": 0,
                     "decorators": [], "bases": [], "args": [],
                     "return_type": "", "calls": [], "raises": [], "complexity": 0})
    public = [d["name"] for d in defs if not d["name"].startswith("_")]
    return {
        "imports": imports, "definitions": defs,
        "exports": [{"type": "implicit", "name": n} for n in public],
        "tags": [], "errors": ["SyntaxError: used regex fallback"],
    }


# ── Public API ────────────────────────────────────────────────────────────────

def _parse_raw(source: str, filepath: str = "") -> dict:
    """Internal — returns raw dicts, not dataclass instances."""
    try:
        tree = ast.parse(source, filename=filepath or "<string>")
    except SyntaxError as e:
        return _regex_parse(source) | {"errors": [f"SyntaxError: {e}"], "tags": []}

    v = _SinglePassVisitor()
    v.visit(tree)

    # Build exports
    if v._all_names is not None:
        # Single export record representing __all__
        exports = [{"type": "__all__", "name": "", "names": v._all_names}]
    else:
        exports = [{"type": "implicit", "name": n}
                   for n in v._top_level_names]

    tags = _detect_tags_from_source(source, v)

    return {
        "imports":     v.imports,
        "exports":     exports,
        "definitions": v.definitions,
        "tags":        tags,
        "errors":      [],
    }



def parse_python(source: str, filepath: str = "") -> dict:
    """
    Parse Python source. Returns dict with typed dataclass instances:
      imports     → [ImportRecord, ...]
      exports     → [ExportRecord, ...]
      definitions → [Definition, ...]  (with args, calls, raises, complexity)
      tags        → [str, ...]
      errors      → [str, ...]
    """
    from graph.types import ImportRecord, ExportRecord, Definition
    raw = _parse_raw(source, filepath)

    imports = [
        ImportRecord(
            type=d.get("type", "import"),
            source=d.get("source", ""),
            line=d.get("line"),
            names=d.get("names", []),
            alias=d.get("alias"),
        ) for d in raw["imports"]
    ]

    exports = [
        ExportRecord(
            type=d.get("type", "implicit"),
            name=d.get("name", ""),
            names=d.get("names", []),
            source=d.get("source"),
            kind=d.get("kind"),
            line=d.get("line"),
        ) for d in raw["exports"]
    ]

    definitions = [
        Definition(
            name=d.get("name", ""),
            kind=d.get("kind", "function"),
            line=d.get("line"),
            end_line=d.get("end_line"),
            indent=d.get("indent", 0),
            is_async=d.get("is_async", False),
            decorators=d.get("decorators", []),
            bases=d.get("bases", []),
            args=d.get("args", []),
            return_type=d.get("return_type", ""),
            calls=d.get("calls", []),
            raises=d.get("raises", []),
            complexity=d.get("complexity", 0),
        ) for d in raw["definitions"]
    ]

    return {
        "imports":     imports,
        "exports":     exports,
        "definitions": definitions,
        "tags":        raw.get("tags", []),
        "errors":      raw.get("errors", []),
    }
