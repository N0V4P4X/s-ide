# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
build/minifier.py
=================
Source code minifier — strips comments, docstrings, and excess whitespace
without changing runtime behaviour.

Supported languages
-------------------
  .py   — strips docstrings (module/class/function), inline comments,
           blank lines; preserves string literals and type hints
  .js/.ts/.mjs/.cjs
        — strips block comments, line comments, collapses whitespace
  .json — strips whitespace (minified JSON)
  .sh   — strips comments and blank lines

The minifier is intentionally conservative: it uses the Python AST
for .py files (accurate) and regex for others (fast but handles common
cases).  It will never strip `# type: ignore` or `# noqa` comments.

Usage
-----
    from build.minifier import minify_file, minify_project, MinifyOptions

    # Single file
    minified = minify_file("src/utils.py")

    # Whole project — writes to a parallel output directory
    opts = MinifyOptions(strip_docstrings=True, strip_comments=True)
    report = minify_project("/my/project", "/my/project/dist", opts)
    # report.files_processed, report.bytes_saved

    # Combine modules into fewer files based on the dependency graph
    from build.minifier import bundle_modules
    bundle_modules(graph_dict, "/my/project", "/out/bundle.py")
"""

from __future__ import annotations
import ast
import json
import os
import re
import shutil
import textwrap
from dataclasses import dataclass, field


@dataclass
class MinifyOptions:
    strip_docstrings:  bool = True   # remove triple-quoted docstrings
    strip_comments:    bool = True   # remove # comments (keeps noqa/type:ignore)
    strip_blank_lines: bool = True   # collapse multiple blank lines to one
    strip_type_hints:  bool = False  # remove annotations (risky — off by default)
    minify_json:       bool = True   # strip whitespace from JSON files


@dataclass
class MinifyReport:
    files_processed: int       = 0
    files_skipped:   int       = 0
    bytes_before:    int       = 0
    bytes_after:     int       = 0
    errors:          list[str] = field(default_factory=list)

    @property
    def bytes_saved(self) -> int:
        """Total bytes removed (before - after)."""
        return self.bytes_before - self.bytes_after

    @property
    def ratio(self) -> float:
        """Compression ratio as a 0–1 float."""
        return round(1 - self.bytes_after / max(self.bytes_before, 1), 3)

    def summary(self) -> str:
        """Return a human-readable summary of minification results."""
        saved = _fmt_size(self.bytes_saved)
        pct   = f"{self.ratio * 100:.1f}%"
        return (f"Processed {self.files_processed} files, "
                f"saved {saved} ({pct})"
                + (f", {len(self.errors)} error(s)" if self.errors else ""))


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


# ── Python minifier ───────────────────────────────────────────────────────────

class _DocstringStripper(ast.NodeTransformer):
    """AST transformer that removes docstring nodes."""

    def _strip_docstring(self, node):
        if (node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            node.body = node.body[1:] or [ast.Pass()]
        return node

    def visit_Module(self, node):
        self.generic_visit(node)
        return self._strip_docstring(node)

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        return self._strip_docstring(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.generic_visit(node)
        return self._strip_docstring(node)


def _minify_python(source: str, opts: MinifyOptions) -> str:
    """
    Minify Python source.  Uses AST for docstrings, regex for comments.
    Falls back to regex-only on SyntaxError.
    """
    result = source

    # 1. Strip docstrings via AST (accurate — won't touch string data)
    if opts.strip_docstrings:
        try:
            tree = ast.parse(source)
            tree = _DocstringStripper().visit(tree)
            ast.fix_missing_locations(tree)
            result = ast.unparse(tree)
        except SyntaxError:
            # Regex fallback: remove triple-quoted strings at statement level
            result = re.sub(
                r'^\s*"""[\s\S]*?"""\s*$',
                "",
                result,
                flags=re.MULTILINE,
            )
            result = re.sub(
                r"^\s*'''[\s\S]*?'''\s*$",
                "",
                result,
                flags=re.MULTILINE,
            )

    # 2. Strip comments — but preserve noqa, type: ignore, pragma
    if opts.strip_comments:
        keep_re = re.compile(r"#\s*(noqa|type:\s*ignore|pragma|pylint)")
        lines = result.splitlines()
        cleaned = []
        for line in lines:
            stripped = line.rstrip()
            if "#" in stripped:
                # Find comment start (outside strings — naive but good enough
                # after docstrings are removed)
                in_str = False
                str_char = None
                for i, ch in enumerate(stripped):
                    if in_str:
                        if ch == str_char:
                            in_str = False
                    elif ch in ('"', "'"):
                        in_str = True
                        str_char = ch
                    elif ch == "#":
                        comment = stripped[i:]
                        if keep_re.search(comment):
                            break   # keep this line as-is
                        stripped = stripped[:i].rstrip()
                        break
            cleaned.append(stripped)
        result = "\n".join(cleaned)

    # 3. Collapse blank lines
    if opts.strip_blank_lines:
        result = re.sub(r"\n{3,}", "\n\n", result)
        result = result.strip() + "\n"

    return result


# ── JS/TS minifier ────────────────────────────────────────────────────────────

def _minify_js(source: str, opts: MinifyOptions) -> str:
    if not opts.strip_comments:
        return source
    # Block comments (keep license blocks starting with /*!)
    result = re.sub(r"/\*(?!!)[\\s\\S]*?\*/", " ", source)
    # Line comments (keep //# sourceMappingURL and // eslint-disable)
    result = re.sub(r"//(?!#|[ \t]*eslint)[^\n]*", "", result)
    if opts.strip_blank_lines:
        result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip() + "\n"


# ── JSON minifier ─────────────────────────────────────────────────────────────

def _minify_json(source: str) -> str:
    try:
        return json.dumps(json.loads(source), separators=(",", ":"))
    except json.JSONDecodeError:
        return source


# ── Shell minifier ────────────────────────────────────────────────────────────

def _minify_shell(source: str, opts: MinifyOptions) -> str:
    if not opts.strip_comments:
        return source
    lines = source.splitlines()
    result = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Keep shebang on first line
        if i == 0 and stripped.startswith("#!"):
            result.append(line)
            continue
        # Remove comment lines and blank lines
        if stripped.startswith("#") or (opts.strip_blank_lines and not stripped):
            continue
        result.append(line)
    return "\n".join(result) + "\n"


# ── File-level dispatch ───────────────────────────────────────────────────────

_MINIFY_FNS = {
    ".py":   lambda s, o: _minify_python(s, o),
    ".pyw":  lambda s, o: _minify_python(s, o),
    ".js":   lambda s, o: _minify_js(s, o),
    ".mjs":  lambda s, o: _minify_js(s, o),
    ".cjs":  lambda s, o: _minify_js(s, o),
    ".ts":   lambda s, o: _minify_js(s, o),
    ".jsx":  lambda s, o: _minify_js(s, o),
    ".tsx":  lambda s, o: _minify_js(s, o),
    ".json": lambda s, o: _minify_json(s) if o.minify_json else s,
    ".sh":   lambda s, o: _minify_shell(s, o),
    ".bash": lambda s, o: _minify_shell(s, o),
}

# Extensions that are binary or shouldn't be touched
_BINARY_EXTS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".tar", ".gz", ".tgz",
    ".exe", ".bin", ".pdf",
}


def minify_file(path: str, opts: MinifyOptions | None = None) -> str:
    """
    Minify a single file and return the result as a string.
    Raises ValueError for unsupported or binary extensions.
    """
    opts = opts or MinifyOptions()
    ext  = os.path.splitext(path)[1].lower()
    if ext in _BINARY_EXTS:
        raise ValueError(f"Binary file, skipping: {path}")
    fn = _MINIFY_FNS.get(ext)
    if fn is None:
        raise ValueError(f"No minifier for extension {ext!r}")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()
    return fn(source, opts)


def minify_project(
    src_dir:  str,
    out_dir:  str,
    opts:     MinifyOptions | None = None,
    skip_dirs: list[str] | None = None,
) -> MinifyReport:
    """
    Minify all supported files under src_dir into out_dir,
    preserving directory structure.

    Files with unsupported extensions are copied verbatim.
    """
    opts      = opts or MinifyOptions()
    src_dir   = os.path.abspath(src_dir)
    out_dir   = os.path.abspath(out_dir)
    skip      = set(skip_dirs or []) | {"versions", ".git", "__pycache__",
                                         "node_modules", ".venv", "venv",
                                         "logs", "dist", "build"}
    report    = MinifyReport()

    os.makedirs(out_dir, exist_ok=True)

    for dirpath, dirnames, filenames in os.walk(src_dir, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in skip]
        rel_dir = os.path.relpath(dirpath, src_dir)
        dest_dir = os.path.join(out_dir, rel_dir)
        os.makedirs(dest_dir, exist_ok=True)

        for fname in filenames:
            src_path  = os.path.join(dirpath, fname)
            dest_path = os.path.join(dest_dir, fname)
            ext       = os.path.splitext(fname)[1].lower()

            try:
                size_before = os.path.getsize(src_path)
                report.bytes_before += size_before

                if ext in _BINARY_EXTS or ext not in _MINIFY_FNS:
                    shutil.copy2(src_path, dest_path)
                    report.bytes_after += size_before
                    report.files_skipped += 1
                else:
                    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
                        source = f.read()
                    minified = _MINIFY_FNS[ext](source, opts)
                    with open(dest_path, "w", encoding="utf-8") as f:
                        f.write(minified)
                    report.bytes_after += len(minified.encode("utf-8"))
                    report.files_processed += 1

            except Exception as e:
                report.errors.append(f"{os.path.relpath(src_path, src_dir)}: {e}")
                report.files_skipped += 1

    return report


# ── Module bundler ────────────────────────────────────────────────────────────

def bundle_modules(
    graph_dict: dict,
    src_dir:    str,
    out_path:   str,
    opts:       MinifyOptions | None = None,
) -> str:
    """
    Combine Python modules into a single file ordered by dependency graph.

    Uses topological sort of the graph edges to order modules so that
    dependencies always appear before their dependents.  Relative imports
    are stripped (modules are now in the same namespace).

    Returns the path written.
    """
    opts    = opts or MinifyOptions()
    src_dir = os.path.abspath(src_dir)

    nodes     = {n["id"]: n for n in graph_dict.get("nodes", [])}
    edges     = graph_dict.get("edges", [])
    py_nodes  = [n for n in nodes.values()
                 if n.get("ext") in (".py", ".pyw") and not n.get("isExternal")]

    # Topological sort
    in_deg   = {n["id"]: 0 for n in py_nodes}
    adj      = {n["id"]: [] for n in py_nodes}
    py_ids   = set(in_deg)
    for e in edges:
        if e["source"] in py_ids and e["target"] in py_ids and not e.get("isExternal"):
            adj[e["source"]].append(e["target"])
            in_deg[e["target"]] += 1

    from collections import deque
    queue  = deque(nid for nid, deg in in_deg.items() if deg == 0)
    order  = []
    while queue:
        nid = queue.popleft()
        order.append(nid)
        for tgt in adj.get(nid, []):
            in_deg[tgt] -= 1
            if in_deg[tgt] == 0:
                queue.append(tgt)
    # Append any remaining (cycles)
    for nid in py_ids:
        if nid not in order:
            order.append(nid)

    parts = [
        "# Auto-generated bundle — do not edit\n"
        f"# Source: {src_dir}\n\n"
    ]
    _rel_import_re = re.compile(r"^from\s+\.[\w.]*\s+import|^import\s+\.", re.MULTILINE)

    for nid in order:
        node = nodes.get(nid)
        if not node:
            continue
        full_path = os.path.join(src_dir, node["path"])
        if not os.path.isfile(full_path):
            continue
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
            src = _rel_import_re.sub("# [relative import removed]", src)
            if opts.strip_docstrings or opts.strip_comments:
                src = _minify_python(src, opts)
            parts.append(f"\n# ── {node['path']} ──\n")
            parts.append(src)
        except Exception as e:
            parts.append(f"# ERROR loading {node['path']}: {e}\n")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out_path

# ── GPLv3 interactive notice ──────────────────────────────────────────────────

_GPLv3_WARRANTY = (
    "THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY\n"
    "APPLICABLE LAW. EXCEPT WHEN OTHERWISE STATED IN WRITING THE COPYRIGHT\n"
    'HOLDERS AND/OR OTHER PARTIES PROVIDE THE PROGRAM \"AS IS\" WITHOUT\n'
    "WARRANTY OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT\n"
    "LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A\n"
    "PARTICULAR PURPOSE. THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE\n"
    "OF THE PROGRAM IS WITH YOU.  (GPL-3.0-or-later §15)"
)

_GPLv3_CONDITIONS = (
    "You may convey verbatim copies of the Program's source code as you\n"
    "receive it, in any medium, provided that you conspicuously and\n"
    "appropriately publish on each copy an appropriate copyright notice and\n"
    "disclaimer of warranty. (See GPL-3.0 §4-6 for full conditions.)\n"
    "Full license: <https://www.gnu.org/licenses/gpl-3.0.html>"
)


def gplv3_notice():
    """Print the short GPLv3 startup notice. Call this at program startup."""
    print("S-IDE  Copyright (C) 2026  N0V4-N3XU5")
    print("This program comes with ABSOLUTELY NO WARRANTY; for details type 'show w'.")
    print("This is free software, and you are welcome to redistribute it")
    print("under certain conditions; type 'show c' for details.")


def gplv3_handle(cmd: str) -> bool:
    """
    Check whether *cmd* is a GPLv3 license command and handle it.
    Returns True if the command was consumed (caller should skip normal processing).
    """
    match cmd.strip().lower():
        case "show w":
            print(_GPLv3_WARRANTY)
            return True
        case "show c":
            print(_GPLv3_CONDITIONS)
            return True
    return False

# ─────────────────────────────────────────────────────────────────────────────
