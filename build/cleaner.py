# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
build/cleaner.py
================
Removes development artifacts from a project before packaging.

What gets cleaned
-----------------
The cleaner operates in named tiers so you can be selective:

  "cache"    — __pycache__, .mypy_cache, .pytest_cache, *.pyc, *.pyo
  "logs"     — logs/, *.log files (NOT the versions/ archive)
  "build"    — dist/, build/, *.egg-info/, .nodegraph.json
  "dev"      — test data, sample files, dev-only scripts (configurable)
  "all"      — all of the above

Usage
-----
    from build.cleaner import clean_project, CleanOptions

    opts = CleanOptions(tiers=["cache", "logs"], dry_run=True)
    report = clean_project("/path/to/project", opts)
    # report.removed  → list of paths removed
    # report.skipped  → list of paths skipped (dry_run or protected)
    # report.freed_bytes → total disk space freed

Each tier's patterns can be overridden via side.project.json:
    {
      "build": {
        "clean": {
          "extra_patterns": ["*.tmp", "scratch/"],
          "protect":        ["test/fixtures/"]
        }
      }
    }
"""

from __future__ import annotations
import fnmatch
import os
import shutil
from dataclasses import dataclass, field
from typing import Literal


Tier = Literal["cache", "logs", "build", "dev", "all"]

# ── Default patterns per tier ─────────────────────────────────────────────────

_TIER_PATTERNS: dict[str, list[str]] = {
    "cache": [
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "*.pyc",
        "*.pyo",
        "*.pyd",
        ".cache",
        "node_modules/.cache",
        ".eslintcache",
        ".tsbuildinfo",
    ],
    "logs": [
        "logs/",
        "*.log",
        "*.log.*",
        "npm-debug.log*",
        "yarn-debug.log*",
        "pip-log.txt",
    ],
    "build": [
        "dist/",
        "build/",
        "*.egg-info/",
        "*.egg",
        ".eggs/",
        ".nodegraph.json",
        "*.spec",            # PyInstaller spec output
        "__pypackages__/",
        ".buildinfo",
    ],
    "dev": [
        "*.test.py",
        "*.spec.py",
        "scratch/",
        "tmp/",
        "sandbox/",
        ".env.local",
        ".env.development",
        "coverage/",
        "htmlcov/",
        ".coverage",
        "*.coverage",
    ],
}

# Directories always protected from deletion regardless of tier
_ALWAYS_PROTECT = {
    ".git", "versions", ".venv", "venv", "env",
}


@dataclass
class CleanOptions:
    tiers:            list[Tier] = field(default_factory=lambda: ["cache"])
    dry_run:          bool       = False   # report without deleting
    extra_patterns:   list[str]  = field(default_factory=list)
    protect:          list[str]  = field(default_factory=list)
    verbose:          bool       = False


@dataclass
class CleanReport:
    removed:      list[str] = field(default_factory=list)
    skipped:      list[str] = field(default_factory=list)
    errors:       list[str] = field(default_factory=list)
    freed_bytes:  int       = 0
    dry_run:      bool      = False

    def summary(self) -> str:
        """Return a human-readable summary string."""
        verb = "Would remove" if self.dry_run else "Removed"
        freed = _fmt_size(self.freed_bytes)
        return (f"{verb} {len(self.removed)} item(s), freed {freed}"
                + (f", {len(self.errors)} error(s)" if self.errors else ""))


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def _matches_any(name: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if name == pat.rstrip("/"):
                return True
        elif fnmatch.fnmatch(name, pat):
            return True
    return False


def _build_patterns(opts: CleanOptions) -> list[str]:
    patterns = []
    tiers = opts.tiers
    if "all" in tiers:
        tiers = list(_TIER_PATTERNS.keys())
    for tier in tiers:
        patterns.extend(_TIER_PATTERNS.get(tier, []))
    patterns.extend(opts.extra_patterns)
    return patterns


def clean_project(root_dir: str, opts: CleanOptions | None = None) -> CleanReport:
    """
    Walk root_dir and remove items matching the selected tier patterns.

    Returns a CleanReport with what was (or would be) removed.
    """
    if opts is None:
        opts = CleanOptions()

    root_dir = os.path.abspath(root_dir)
    patterns = _build_patterns(opts)
    protect  = set(opts.protect) | _ALWAYS_PROTECT
    report   = CleanReport(dry_run=opts.dry_run)

    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=True):
        rel_dir = os.path.relpath(dirpath, root_dir)

        # Prune protected dirs from traversal
        dirnames[:] = [
            d for d in dirnames
            if d not in protect
            and not _matches_any(d, [p for p in protect])
        ]

        # Check directories themselves
        for d in list(dirnames):
            if _matches_any(d, patterns):
                full = os.path.join(dirpath, d)
                rel  = os.path.relpath(full, root_dir)
                if _is_protected(rel, protect):
                    report.skipped.append(rel)
                    dirnames.remove(d)
                    continue
                size = _dir_size(full)
                if opts.dry_run:
                    report.removed.append(rel)
                    report.freed_bytes += size
                    dirnames.remove(d)
                else:
                    try:
                        shutil.rmtree(full)
                        report.removed.append(rel)
                        report.freed_bytes += size
                        dirnames.remove(d)
                        if opts.verbose:
                            print(f"  [rm dir]  {rel}")
                    except OSError as e:
                        report.errors.append(f"{rel}: {e}")

        # Check files
        for fname in filenames:
            if _matches_any(fname, patterns):
                full = os.path.join(dirpath, fname)
                rel  = os.path.relpath(full, root_dir)
                if _is_protected(rel, protect):
                    report.skipped.append(rel)
                    continue
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                if opts.dry_run:
                    report.removed.append(rel)
                    report.freed_bytes += size
                else:
                    try:
                        os.remove(full)
                        report.removed.append(rel)
                        report.freed_bytes += size
                        if opts.verbose:
                            print(f"  [rm file] {rel}")
                    except OSError as e:
                        report.errors.append(f"{rel}: {e}")

    return report


def _is_protected(rel_path: str, protect: set) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    for p in protect:
        if p in parts:
            return True
    return False

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
