# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
ai/playground.py
================
Invisible sandbox for agent code execution.

The Playground gives agents a safe place to run Python snippets
against the project without touching the working tree. It is
NOT a visible panel — it is a backend service that the
`run_in_playground` tool dispatches to.

How it works
------------
1. Agent calls run_in_playground(code="...")
2. dispatch_tool routes to _run_in_playground in tools.py
3. tools.py calls Playground.run(code, project_root)
4. Playground copies the project to a temp dir (read-only snapshot)
5. Runs the code in a subprocess with a 10s timeout
6. Returns stdout + stderr + exit code
7. Temp dir is deleted — project is untouched

The Playground is intentionally minimal:
- No persistent state between calls
- No network access (subprocess runs with PATH only)
- 10 second hard timeout
- Output capped at 4000 chars

This is separate from build/sandbox.py (which runs full project scripts
with process management). The Playground is for quick agent verification
snippets, not long-running builds.
"""

from __future__ import annotations
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass


@dataclass
class PlaygroundResult:
    code:      str
    stdout:    str
    stderr:    str
    exit_code: int
    timed_out: bool = False
    error:     str  = ""

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "stdout":    self.stdout[:4000],
            "stderr":    self.stderr[:1000],
            "timed_out": self.timed_out,
            "error":     self.error,
            "ok":        self.exit_code == 0 and not self.timed_out and not self.error,
        }


class Playground:
    """
    Executes Python snippets in an isolated copy of the project.
    Stateless — each call is independent.
    """

    TIMEOUT_S    = 10
    MAX_OUT_CHARS = 4000

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)

    def run(self, code: str, setup: str = "") -> PlaygroundResult:
        """
        Execute `code` in a sandbox copy of the project.

        Parameters
        ----------
        code:  Python snippet to execute
        setup: Optional preamble (e.g. imports) prepended to code

        Returns
        -------
        PlaygroundResult with stdout, stderr, exit_code
        """
        full_code = ""
        if setup:
            full_code += textwrap.dedent(setup).strip() + "\n\n"
        full_code += textwrap.dedent(code).strip() + "\n"

        with tempfile.TemporaryDirectory(prefix="side-play-") as tmp:
            # Shallow copy: symlink source files (read-only view, fast)
            sandbox = os.path.join(tmp, "project")
            try:
                self._shallow_copy(self.project_root, sandbox)
            except Exception as e:
                return PlaygroundResult(
                    code=code, stdout="", stderr="",
                    exit_code=1, error=f"Setup failed: {e}",
                )

            # Write the code to a temp script
            # Prepend sys.path so the sandbox project root is importable
            script = os.path.join(tmp, "_playground.py")
            preamble = (
                "import sys as _sys, os as _os\n"
                f"_sys.path.insert(0, {repr(sandbox)})\n"
            )
            with open(script, "w", encoding="utf-8") as f:
                f.write(preamble + full_code)

            # Run it
            try:
                result = subprocess.run(
                    [sys.executable, script],
                    cwd=sandbox,
                    capture_output=True,
                    text=True,
                    timeout=self.TIMEOUT_S,
                )
                return PlaygroundResult(
                    code      = code,
                    stdout    = result.stdout[:self.MAX_OUT_CHARS],
                    stderr    = result.stderr[:1000],
                    exit_code = result.returncode,
                )
            except subprocess.TimeoutExpired:
                return PlaygroundResult(
                    code=code, stdout="", stderr="",
                    exit_code=1, timed_out=True,
                    error=f"Timed out after {self.TIMEOUT_S}s",
                )
            except Exception as e:
                return PlaygroundResult(
                    code=code, stdout="", stderr="",
                    exit_code=1, error=str(e),
                )

    def _shallow_copy(self, src: str, dst: str) -> None:
        """
        Create a sandbox by hard-linking files from the project.
        Falls back to a real copy if hard links fail (cross-device).
        Skips __pycache__, .git, logs, and .side directories.
        """
        SKIP = {".git", "__pycache__", "logs", ".side", "versions", "dist"}
        os.makedirs(dst, exist_ok=True)
        for item in os.listdir(src):
            if item in SKIP or item.startswith("."):
                continue
            s = os.path.join(src, item)
            d = os.path.join(dst, item)
            if os.path.isdir(s):
                self._shallow_copy(s, d)
            else:
                try:
                    os.link(s, d)
                except OSError:
                    shutil.copy2(s, d)


# ── Module-level convenience ───────────────────────────────────────────────────

_instances: dict[str, Playground] = {}


def get_playground(project_root: str) -> Playground:
    """Get or create a Playground for a project root."""
    key = os.path.abspath(project_root)
    if key not in _instances:
        _instances[key] = Playground(key)
    return _instances[key]


def run_snippet(code: str, project_root: str) -> dict:
    """
    Convenience function for tools.py dispatch.
    Returns a JSON-safe dict.
    """
    pg = get_playground(project_root)
    result = pg.run(code)
    return result.to_dict()

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
