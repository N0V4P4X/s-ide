# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
parser/walker.py
================
Directory traversal and file discovery for the project parser.

Produces a flat list of FileInfo objects — one per discovered source file.
Handles global ignore patterns plus per-project overrides from
side.project.json. Glob-style patterns are supported (prefix * only).

FILE_CATEGORIES maps extensions to visual grouping names used by the
node editor. Files without a known extension are categorised as 'other'
and are still included in the graph (they show up as gray nodes).
"""

from __future__ import annotations
import os
import re
from dataclasses import dataclass
from fnmatch import fnmatch

# ── Always-ignored names/patterns ─────────────────────────────────────────────
# Applied regardless of project config.
GLOBAL_IGNORE: list[str] = [
    # dependency and build artifacts
    "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "coverage",
    ".cache", ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
    # minified / bundled
    "*.min.js", "*.bundle.js",
    # s-ide internals
    ".nodegraph.json", "side.project.json",
    # version archives — snapshots, not live source
    "versions", "releases", "archive", "archives",
    "VERSIONS", "RELEASES", "ARCHIVE",
    # git internals
    ".git",
    # common upload dirs
    "uploads",
    # egg-info
    "*.egg-info",
]

# ── Extension → visual category ───────────────────────────────────────────────
FILE_CATEGORIES: dict[str, str] = {
    ".py":   "python",
    ".pyw":  "python",
    ".js":   "javascript",
    ".mjs":  "javascript",
    ".cjs":  "javascript",
    ".jsx":  "react",
    ".tsx":  "react",
    ".ts":   "typescript",
    ".json": "config",
    ".toml": "config",
    ".yaml": "config",
    ".yml":  "config",
    ".env":  "config",
    ".ini":  "config",
    ".cfg":  "config",
    ".sh":   "shell",
    ".bash": "shell",
    ".zsh":  "shell",
    ".fish": "shell",
    ".css":  "style",
    ".scss": "style",
    ".less": "style",
    ".md":   "docs",
    ".mdx":  "docs",
    ".rst":  "docs",
    ".txt":  "docs",
    ".html": "markup",
    ".htm":  "markup",
    ".sql":  "database",
    ".go":   "go",
    ".rs":   "rust",
    ".rb":   "ruby",
    ".lua":  "lua",
}

# ── Parseable extensions (have a dedicated parser) ───────────────────────────
PARSEABLE_EXTENSIONS: set[str] = {
    ".py", ".pyw",
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
    ".json",
    ".sh", ".bash", ".zsh",
}


@dataclass
class FileInfo:
    """Metadata for a single discovered file."""
    full_path: str
    relative_path: str   # relative to project root, forward slashes
    name: str
    ext: str             # lowercase, with dot (e.g. '.py')
    category: str        # from FILE_CATEGORIES, or 'other'
    is_parseable: bool   # True if a language parser exists for this ext


def _should_ignore(name: str, extra_patterns: list[str]) -> bool:
    """
    Return True if a file/directory name matches any ignore pattern.
    Patterns starting with '.' match hidden files/dirs automatically.
    Glob patterns (e.g. '*.min.js') use fnmatch.
    """
    if name.startswith("."):
        return True
    all_patterns = GLOBAL_IGNORE + extra_patterns
    for pattern in all_patterns:
        if pattern.startswith("*"):
            if fnmatch(name, pattern):
                return True
        elif name == pattern:
            return True
    return False


def walk_directory(
    root_dir: str,
    extra_ignore: list[str] | None = None,
) -> list[FileInfo]:
    """
    Recursively walk root_dir, returning one FileInfo per discovered file.
    extra_ignore: additional patterns from side.project.json["ignore"].
    """
    extra = extra_ignore or []
    files: list[FileInfo] = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Prune ignored directories in-place (modifying dirnames controls os.walk)
        dirnames[:] = sorted(
            d for d in dirnames if not _should_ignore(d, extra)
        )

        for filename in sorted(filenames):
            if _should_ignore(filename, extra):
                continue

            full_path = os.path.join(dirpath, filename)
            raw_rel = os.path.relpath(full_path, root_dir)
            # Normalise to forward slashes for cross-platform consistency
            relative_path = raw_rel.replace(os.sep, "/")

            ext = os.path.splitext(filename)[1].lower()
            category = FILE_CATEGORIES.get(ext, "other")
            is_parseable = ext in PARSEABLE_EXTENSIONS

            files.append(FileInfo(
                full_path=full_path,
                relative_path=relative_path,
                name=filename,
                ext=ext,
                category=category,
                is_parseable=is_parseable,
            ))

    return files


def make_node_id(relative_path: str) -> str:
    """
    Derive a stable node ID from a relative file path.
    Replaces non-alphanumeric characters with underscores.
    """
    return re.sub(r"[^a-zA-Z0-9]", "_", relative_path)

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
