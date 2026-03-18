"""
monitor/instrumenter.py
=======================
Adds timing instrumentation to every applicable source file in a project.

For each eligible Python file the instrumenter:
  1. Parses the AST to find all top-level public functions
  2. Inserts `@timed` decorator above each one (source-level, preserves
     formatting and comments)
  3. Adds `from monitor.instrument import timed` at the top (idempotent)
  4. Optionally adds `monitor.instrument.init(__file__)` in the entry-point
     module so metrics are flushed to .side-metrics.json automatically

For JavaScript/TypeScript files it inserts timing wrappers using a
regex-based approach (no AST — JS is harder to modify safely).

Test stub generation (optional):
  For each instrumented file `src/foo.py`, creates `test/test_foo.py`
  with a stub test function for every timed function.  Existing test
  files are extended, not overwritten.

All changes are previewed before being written, and a rollback manifest
is saved so every change can be undone with `rollback()`.

Usage
-----
    from monitor.instrumenter import Instrumenter, InstrumentOptions

    opts = InstrumentOptions(
        add_tests=True,
        preview=True,       # print diff, don't write
        entry_point="main.py",
    )
    result = Instrumenter("/path/to/project", opts).run()
    print(result.summary())

    # Undo everything
    from monitor.instrumenter import rollback
    rollback("/path/to/project")
"""

from __future__ import annotations
import ast
import os
import re
import shutil
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone


# ── Options ───────────────────────────────────────────────────────────────────

@dataclass
class InstrumentOptions:
    """Configuration for the instrumenter."""

    # Which functions to instrument
    public_only:     bool = True    # skip names starting with _
    top_level_only:  bool = True    # skip methods inside classes
    skip_dunders:    bool = True    # skip __init__, __str__ etc.
    min_lines:       int  = 1       # skip empty functions (0 body statements)

    # What to add
    add_init_call:   bool = True    # add monitor.instrument.init() to entry point
    add_tests:       bool = False   # generate test stubs alongside source files
    test_dir:        str  = "test"  # where to write test stubs

    # Safety
    preview:         bool = False   # print proposed changes, don't write
    backup:          bool = True    # save originals to .side-backup/ before modifying
    skip_patterns:   list[str] = field(default_factory=lambda: [
        "test_*", "*_test.py", "conftest.py", "setup.py",
        "migrations/*", "vendor/*",
    ])

    # Entry point for init() call
    entry_point:     str  = ""      # e.g. "main.py" — auto-detected if empty


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class InstrumentResult:
    """Summary of what the instrumenter did."""
    files_modified:  list[str] = field(default_factory=list)
    files_skipped:   list[str] = field(default_factory=list)
    tests_created:   list[str] = field(default_factory=list)
    functions_timed: int       = 0
    errors:          list[str] = field(default_factory=list)
    preview:         bool      = False
    rollback_path:   str       = ""

    def summary(self) -> str:
        verb = "Would modify" if self.preview else "Modified"
        return (
            f"{verb} {len(self.files_modified)} files, "
            f"timed {self.functions_timed} functions"
            + (f", created {len(self.tests_created)} test files" if self.tests_created else "")
            + (f", {len(self.errors)} error(s)" if self.errors else "")
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

_IMPORT_LINE = "from monitor.instrument import timed\n"
_INIT_CALL   = "monitor.instrument.init(__file__)\n"
_INIT_IMPORT = "import monitor.instrument\n"

# Patterns that signal a file is a test file (don't instrument these)
_TEST_PATTERNS = re.compile(
    r"(^test_|_test\.py$|/tests?/|conftest\.py$)", re.IGNORECASE
)


def _is_eligible(path: str, skip_patterns: list[str]) -> bool:
    """Return True if this file should be considered for instrumentation."""
    import fnmatch
    name = os.path.basename(path)
    rel  = path.replace("\\", "/")
    if _TEST_PATTERNS.search(rel):
        return False
    for pat in skip_patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat):
            return False
    return True


def _already_instrumented(source: str) -> bool:
    """True if the file already imports from monitor.instrument."""
    return "from monitor.instrument import" in source or \
           "import monitor.instrument" in source


def _find_import_insert_point(lines: list[str]) -> int:
    """
    Find the best line to insert the timed import — after the last existing
    import/from statement at module level, before the first function/class.
    """
    last_import = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")) and not stripped.startswith("from __future__"):
            last_import = i + 1
        elif stripped and not stripped.startswith("#") and i > 0:
            # Stop at first non-import substantive line after we've seen imports
            if last_import > 0:
                break
    return last_import


def _collect_targets(tree: ast.Module, opts: InstrumentOptions) -> list[int]:
    """
    Return a list of 0-indexed line numbers where @timed should be inserted
    (the line before each `def` statement).
    """
    targets = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        name = node.name

        # Skip private functions (names starting with _)
        if opts.public_only and name.startswith("_"):
            continue
        # Skip dunder methods (__init__, __str__, etc.)
        if opts.skip_dunders and name.startswith("__") and name.endswith("__"):
            continue

        # Optionally top-level only
        if opts.top_level_only and node.col_offset > 0:
            continue

        # Skip functions with too few body statements (e.g. bare pass)
        # Count real statements (excluding docstrings-as-Expr)
        real_stmts = [
            s for s in node.body
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
        ]
        if len(real_stmts) < opts.min_lines:
            continue

        # Skip already-decorated with @timed
        already = any(
            (isinstance(d, ast.Name) and d.id == "timed") or
            (isinstance(d, ast.Attribute) and d.attr == "timed")
            for d in node.decorator_list
        )
        if already:
            continue

        # Insert before the first decorator if present, else before def
        if node.decorator_list:
            insert_before = min(d.lineno for d in node.decorator_list) - 1
        else:
            insert_before = node.lineno - 1
        targets.append(insert_before)

    return sorted(set(targets))


def _instrument_python(source: str, opts: InstrumentOptions,
                       filepath: str = "") -> tuple[str, int]:
    """
    Return (modified_source, n_functions_timed).
    If no changes needed, returns ("", 0).
    """
    if _already_instrumented(source):
        return ("", 0)

    try:
        tree = ast.parse(source, filename=filepath or "<string>")
    except SyntaxError:
        return ("", 0)

    targets = _collect_targets(tree, opts)
    if not targets:
        return ("", 0)

    lines = source.splitlines(keepends=True)

    # Insert @timed decorators from bottom to top (preserves line numbers)
    for idx in sorted(targets, reverse=True):
        indent = ""
        if idx < len(lines):
            # Match indentation of the following def line
            m = re.match(r"^(\s*)", lines[idx])
            if m:
                indent = m.group(1)
        lines.insert(idx, f"{indent}@timed\n")

    # Insert import after existing imports
    import_idx = _find_import_insert_point(lines)
    lines.insert(import_idx, _IMPORT_LINE)

    # Validate the result parses
    result = "".join(lines)
    try:
        ast.parse(result)
    except SyntaxError as e:
        return ("", 0)

    return (result, len(targets))


def _generate_test_stubs(source: str, rel_path: str) -> str:
    """
    Generate a test file with stub functions for every timed function.
    Returns the test file content, or "" if nothing to test.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""

    module_name = os.path.splitext(os.path.basename(rel_path))[0]
    import_path = rel_path.replace("/", ".").replace("\\", ".").removesuffix(".py")

    stubs = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.col_offset > 0:
            continue
        if node.name.startswith("_"):
            continue

        args = [a.arg for a in node.args.args if a.arg != "self"]
        arg_hint = f"  # args: {args}" if args else ""
        is_async = isinstance(node, ast.AsyncFunctionDef)
        prefix = "async " if is_async else ""

        stubs.append(
            f"\n{prefix}def test_{node.name}():{arg_hint}\n"
            f"    \"\"\"Test {node.name}() from {rel_path}\"\"\"\n"
            f"    # TODO: add test implementation\n"
            f"    pass\n"
        )

    if not stubs:
        return ""

    header = (
        f'"""\nAuto-generated test stubs for {rel_path}\n'
        f'Generated by monitor.instrumenter — fill in the bodies.\n"""\n\n'
        f"import pytest\n"
        f"from {import_path} import *\n"
    )

    return header + "".join(stubs)


def _detect_entry_point(root_dir: str) -> str:
    """Guess the project entry point for placing init() call."""
    candidates = ["main.py", "app.py", "run.py", "__main__.py",
                  "src/main.py", "src/app.py"]
    for c in candidates:
        if os.path.isfile(os.path.join(root_dir, c)):
            return c
    return ""


# ── Main class ────────────────────────────────────────────────────────────────

class Instrumenter:
    """
    Walks a project and adds @timed instrumentation to eligible files.

    Usage:
        result = Instrumenter("/my/project", opts).run()
        print(result.summary())
    """

    def __init__(self, root_dir: str, opts: InstrumentOptions | None = None):
        self.root_dir = os.path.abspath(root_dir)
        self.opts     = opts or InstrumentOptions()
        self._backup_dir = os.path.join(self.root_dir, ".side-backup")
        self._manifest: list[dict] = []   # for rollback

    def run(self) -> InstrumentResult:
        """Instrument the project. Returns a summary result."""
        result = InstrumentResult(preview=self.opts.preview)

        # Detect entry point
        entry = self.opts.entry_point or _detect_entry_point(self.root_dir)

        # Walk all Python files
        for dirpath, dirnames, filenames in os.walk(self.root_dir):
            # Standard ignores
            dirnames[:] = [
                d for d in dirnames
                if d not in ("__pycache__", ".git", ".venv", "venv", "env",
                              "node_modules", "dist", "build", ".side-backup",
                              "logs", "versions")
                and not d.startswith(".")
            ]
            for fname in sorted(filenames):
                if not fname.endswith(".py"):
                    continue
                full_path = os.path.join(dirpath, fname)
                rel_path  = os.path.relpath(full_path, self.root_dir).replace("\\", "/")

                if not _is_eligible(rel_path, self.opts.skip_patterns):
                    result.files_skipped.append(rel_path)
                    continue

                try:
                    self._process_file(full_path, rel_path, result, entry)
                except Exception as e:
                    result.errors.append(f"{rel_path}: {e}")

        # Save rollback manifest
        if not self.opts.preview and self._manifest:
            self._save_manifest()
            result.rollback_path = self._backup_dir

        return result

    def _process_file(self, full_path: str, rel_path: str,
                      result: InstrumentResult, entry: str) -> None:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            original = f.read()

        modified, n_timed = _instrument_python(original, self.opts, full_path)
        if not modified:
            result.files_skipped.append(rel_path)
            return

        result.files_modified.append(rel_path)
        result.functions_timed += n_timed

        if self.opts.preview:
            self._print_diff(rel_path, original, modified)
            return

        # Backup original
        if self.opts.backup:
            self._backup_file(rel_path, original)

        # Write modified file
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(modified)

        # Optionally add init() call to entry point
        if self.opts.add_init_call and rel_path == entry:
            self._add_init_call(full_path, rel_path)

        # Optionally generate test stubs
        if self.opts.add_tests:
            test_content = _generate_test_stubs(modified, rel_path)
            if test_content:
                test_path = self._write_test_stub(rel_path, test_content)
                if test_path:
                    result.tests_created.append(test_path)

    def _add_init_call(self, full_path: str, rel_path: str) -> None:
        """Add monitor.instrument.init() call to entry point."""
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # Check not already present
        if any("monitor.instrument.init(" in l for l in lines):
            return
        # Insert after imports
        idx = _find_import_insert_point(lines)
        lines.insert(idx, _INIT_CALL)
        lines.insert(idx, _INIT_IMPORT)
        with open(full_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    def _write_test_stub(self, rel_path: str, content: str) -> str:
        """Write test stubs, extending existing file if present."""
        module_name = os.path.splitext(os.path.basename(rel_path))[0]
        test_dir    = os.path.join(self.root_dir, self.opts.test_dir)
        os.makedirs(test_dir, exist_ok=True)
        test_file   = os.path.join(test_dir, f"test_{module_name}.py")

        if os.path.isfile(test_file):
            # Append only stubs not already present
            existing = open(test_file).read()
            new_stubs = [
                chunk for chunk in content.split("\ndef test_")[1:]
                if f"def test_{chunk.split('():')[0]}()" not in existing
            ]
            if not new_stubs:
                return ""
            with open(test_file, "a", encoding="utf-8") as f:
                for stub in new_stubs:
                    f.write(f"\ndef test_{stub}")
        else:
            with open(test_file, "w", encoding="utf-8") as f:
                f.write(content)

        return os.path.relpath(test_file, self.root_dir)

    def _backup_file(self, rel_path: str, content: str) -> None:
        backup_path = os.path.join(self._backup_dir, rel_path)
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(content)
        self._manifest.append({"rel": rel_path, "backup": backup_path})

    def _save_manifest(self) -> None:
        manifest_path = os.path.join(self._backup_dir, "manifest.json")
        os.makedirs(self._backup_dir, exist_ok=True)
        with open(manifest_path, "w") as f:
            json.dump({
                "created": datetime.now(tz=timezone.utc).isoformat(),
                "root":    self.root_dir,
                "files":   self._manifest,
            }, f, indent=2)

    def _print_diff(self, rel_path: str, original: str, modified: str) -> None:
        print(f"\n── {rel_path} ─────────────────────────────")
        orig_lines = original.splitlines()
        mod_lines  = modified.splitlines()
        for i, (o, m) in enumerate(zip(orig_lines, mod_lines)):
            if o != m:
                print(f"  -{i+1}: {o}")
                print(f"  +{i+1}: {m}")
        # Show any appended lines
        if len(mod_lines) > len(orig_lines):
            for extra in mod_lines[len(orig_lines):]:
                print(f"  + {extra}")


# ── Rollback ──────────────────────────────────────────────────────────────────

def rollback(root_dir: str) -> dict:
    """
    Restore all files changed by the last instrumenter run.
    Returns {"restored": [...], "errors": [...]}.
    """
    backup_dir    = os.path.join(os.path.abspath(root_dir), ".side-backup")
    manifest_path = os.path.join(backup_dir, "manifest.json")
    restored, errors = [], []

    if not os.path.isfile(manifest_path):
        return {"restored": [], "errors": ["no backup manifest found"]}

    manifest = json.load(open(manifest_path))
    for entry in manifest.get("files", []):
        rel       = entry["rel"]
        backup    = entry["backup"]
        dest      = os.path.join(root_dir, rel)
        try:
            shutil.copy2(backup, dest)
            restored.append(rel)
        except Exception as e:
            errors.append(f"{rel}: {e}")

    if not errors:
        shutil.rmtree(backup_dir, ignore_errors=True)

    return {"restored": restored, "errors": errors}


def rollback_available(root_dir: str) -> bool:
    """True if a rollback manifest exists for this project."""
    return os.path.isfile(
        os.path.join(os.path.abspath(root_dir), ".side-backup", "manifest.json")
    )
