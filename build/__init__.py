# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
build/
======
S-IDE build pipeline — clean, minify, and package projects.

Modules
-------
cleaner.py   — remove dev artifacts, caches, logs
minifier.py  — strip comments/docstrings, combine modules
packager.py  — produce portable directories, tarballs, or platform installers

Quick usage
-----------
    from build.cleaner  import clean_project, CleanOptions
    from build.minifier import minify_project, MinifyOptions
    from build.packager import package_project, PackageOptions

    # Full build pipeline
    clean_project(root, CleanOptions(tiers=["cache", "logs"]))
    minify_project(root, root + "/dist/src", MinifyOptions())
    result = package_project(root, root + "/dist",
                              PackageOptions(kind="tarball", minify=True))
    print(result.summary())
"""

from .cleaner  import clean_project,   CleanOptions,   CleanReport
from .minifier import minify_project,  MinifyOptions,  MinifyReport,  minify_file
from .packager import package_project, PackageOptions, PackageResult

from .sandbox import SandboxRun, SandboxOptions, list_sandbox_logs

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
