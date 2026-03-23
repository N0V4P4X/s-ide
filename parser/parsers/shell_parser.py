# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
parser/parsers/shell_parser.py
===============================
Extracts relationships from shell scripts (.sh, .bash, .zsh, .fish).

Captured relationships:
  source / .   → treated as imports (file sourcing)
  bash/sh X.sh → treated as imports (script execution)
  export VAR=  → treated as exports (environment variables)
  function f() → definitions

Tags: systemd, docker, package-manager, remote, http-client, shebang
"""

from __future__ import annotations
import re
from graph.types import ImportRecord, ExportRecord, Definition


def _line_of(source: str, index: int) -> int:
    return source[:index].count("\n") + 1


def parse_shell(source: str, file_path: str = "") -> dict:
    """
    Parse a shell script and return semantic records.
    """
    imports: list[ImportRecord] = []
    exports: list[ExportRecord] = []
    definitions: list[Definition] = []
    tags: list[str] = []
    errors: list[str] = []

    # Shebang tag
    shebang_m = re.match(r"^#!(.+)", source)
    if shebang_m:
        tags.append(f"shebang:{shebang_m.group(1).strip()}")

    # Strip comments for relationship extraction
    stripped = re.sub(r"#[^\n]*", "", source)

    # source ./file.sh  or  . ./file.sh
    for m in re.finditer(r"^(?:source|\.)\s+([^\s#]+)", stripped, re.MULTILINE):
        path = m.group(1).strip()
        imports.append(ImportRecord(type="source", source=path,
                                    line=_line_of(source, m.start())))

    # bash script.sh  /  sh script.sh  /  ./script.sh
    for m in re.finditer(
        r"\b((?:bash|sh)\s+[\w./]+\.sh|\.\/[\w./]+\.sh)",
        stripped
    ):
        path = m.group(1).strip()
        imports.append(ImportRecord(type="script-call", source=path,
                                    line=_line_of(source, m.start())))

    # export VAR=value  /  VAR=value
    for m in re.finditer(
        r"^(?:export\s+)?([A-Z_][A-Z0-9_]{2,})\s*=",
        stripped, re.MULTILINE
    ):
        exports.append(ExportRecord(type="env-var", name=m.group(1),
                                    line=_line_of(source, m.start())))

    # function definitions:  function name() {  /  name() {
    for m in re.finditer(
        r"^(?:function\s+)?(\w+)\s*\(\s*\)\s*\{",
        stripped, re.MULTILINE
    ):
        definitions.append(Definition(name=m.group(1), kind="shell-function",
                                      line=_line_of(source, m.start())))

    # Tags
    if re.search(r"\bsystemctl\b|\bservice\s", source):      tags.append("systemd")
    if re.search(r"\bdocker\b|\bdocker-compose\b", source):  tags.append("docker")
    if re.search(r"\bapt\b|\bapt-get\b|\byum\b|\bpacman\b|\bdnf\b", source):
        tags.append("package-manager")
    if re.search(r"\bssh\b|\bscp\b|\brsync\b", source):      tags.append("remote")
    if re.search(r"\bcurl\s|\bwget\s", source):               tags.append("http-client")

    return {
        "imports":     imports,
        "exports":     exports,
        "definitions": definitions,
        "tags":        tags,
        "errors":      errors,
    }

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
