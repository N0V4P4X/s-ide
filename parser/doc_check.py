# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
parser/doc_check.py
====================
Audits a parsed project for documentation health and emits warnings
that the node editor renders as badges on files and directories.

Checks performed
-----------------
1. MISSING README  — a directory that contains source files but has no README.md
2. STALE README    — a README.md that is older than one or more files in its dir
3. EMPTY MODULE    — a source file with no imports, exports, or definitions
                     (likely a forgotten stub or accidentally blank file)

Returns a DocAudit object that is embedded in the graph's meta section.
"""

from __future__ import annotations
import os
from datetime import datetime
from graph.types import FileNode, DocAudit, DocWarning

# Categories that count as 'source' for doc-check purposes
_SOURCE_CATEGORIES = {"python", "javascript", "typescript", "react", "shell"}
# Extensions excluded from the 'source file' count inside a directory
_IGNORED_EXTS = {".md", ".mdx", ".txt", ".lock", ".log", ".png", ".jpg", ".ico"}


def _mtime_ts(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def audit_docs(root_dir: str, nodes: list[FileNode]) -> DocAudit:
    """
    Run all documentation health checks.

    root_dir  -- project root (used only for display; not re-walked)
    nodes     -- already-parsed FileNode list from project_parser
    """
    warnings: list[DocWarning] = []

    # Build per-directory view: dir_rel → {readme_mtime, readme_path, source_files}
    dir_map: dict[str, dict] = {}

    for node in nodes:
        dir_rel = os.path.dirname(node.path).replace("\\", "/") or "."

        if dir_rel not in dir_map:
            dir_map[dir_rel] = {
                "readme_mtime": None,
                "readme_path": None,
                "source_files": [],
            }

        entry = dir_map[dir_rel]
        filename = os.path.basename(node.path).lower()

        if filename == "readme.md":
            entry["readme_mtime"] = _mtime_ts(node.modified)
            entry["readme_path"] = node.path
        elif node.ext not in _IGNORED_EXTS:
            entry["source_files"].append({
                "id": node.id,
                "path": node.path,
                "mtime": _mtime_ts(node.modified),
            })

    # ── Check 1: Missing README ───────────────────────────────────────────────
    for dir_rel, entry in dir_map.items():
        if not entry["source_files"]:
            continue   # dir has no source — skip
        if entry["readme_path"] is None:
            warnings.append(DocWarning(
                type="missing-readme",
                severity="warning",
                dir=dir_rel,
                message=f"No README.md in '{dir_rel}'",
                affected_files=[f["id"] for f in entry["source_files"]],
            ))
            continue

        # ── Check 2: Stale README ─────────────────────────────────────────────
        stale = [
            f for f in entry["source_files"]
            if f["mtime"] is not None and f["mtime"] > entry["readme_mtime"]
        ]
        if stale:
            latest_ts = max(f["mtime"] for f in stale)
            warnings.append(DocWarning(
                type="stale-readme",
                severity="info",
                dir=dir_rel,
                readme_path=entry["readme_path"],
                message=f"README.md in '{dir_rel}' is older than {len(stale)} file(s)",
                affected_files=[f["id"] for f in stale],
                stale_since=datetime.fromtimestamp(latest_ts).isoformat(),
            ))

    # ── Check 3: Empty module ─────────────────────────────────────────────────
    for node in nodes:
        if node.category not in _SOURCE_CATEGORIES:
            continue
        no_imports = len(node.imports) == 0
        no_exports = len(node.exports) == 0
        no_defs    = len(node.definitions) == 0
        if no_imports and no_exports and no_defs:
            warnings.append(DocWarning(
                type="empty-module",
                severity="info",
                node_id=node.id,
                message=f"'{node.path}' has no imports, exports, or definitions",
                affected_files=[node.id],
            ))

    missing  = sum(1 for w in warnings if w.type == "missing-readme")
    stale    = sum(1 for w in warnings if w.type == "stale-readme")
    empty    = sum(1 for w in warnings if w.type == "empty-module")

    return DocAudit(
        healthy=len(warnings) == 0,
        missing_readmes=missing,
        stale_readmes=stale,
        empty_modules=empty,
        total_warnings=len(warnings),
        warnings=warnings,
    )

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
