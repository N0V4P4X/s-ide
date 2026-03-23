# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
parser/parsers/json_parser.py
==============================
Extracts structural information from JSON files.

Handles:
  - package.json   → npm dependencies, scripts, entry points
  - pyproject.toml (as JSON if pre-parsed) — not applicable here
  - tsconfig.json  → path aliases
  - *.config.json / *settings* → generic key extraction
  - JSON Schema    → $schema tag

Returns the same {imports, exports, definitions, tags, errors} shape
as all other parsers so the orchestrator can treat them uniformly.
"""

from __future__ import annotations
import json
import os
from graph.types import ImportRecord, ExportRecord, Definition


def parse_json(source: str, file_path: str = "") -> dict:
    """
    Parse a JSON file and return semantic records.
    """
    imports: list[ImportRecord] = []
    exports: list[ExportRecord] = []
    definitions: list[Definition] = []
    tags: list[str] = []
    errors: list[str] = []

    try:
        data = json.loads(source)
    except json.JSONDecodeError as exc:
        return {"imports": imports, "exports": exports,
                "definitions": definitions, "tags": tags,
                "errors": [f"JSON parse error: {exc}"]}

    name = os.path.basename(file_path)

    # ── package.json ──────────────────────────────────────────────────────────
    if name == "package.json" and isinstance(data, dict):
        tags.append("package-manifest")
        if pkg_name := data.get("name"):
            tags.append(f"pkg:{pkg_name}")

        for dep, version in (data.get("dependencies") or {}).items():
            imports.append(ImportRecord(type="npm-dependency", source=dep,
                                        names=[version]))
        for dep, version in (data.get("devDependencies") or {}).items():
            imports.append(ImportRecord(type="npm-dev-dependency", source=dep,
                                        names=[version]))

        if main := data.get("main"):
            exports.append(ExportRecord(type="main-entry", name=main))
        if module := data.get("module"):
            exports.append(ExportRecord(type="module-entry", name=module))

        for script_name, cmd in (data.get("scripts") or {}).items():
            definitions.append(Definition(name=script_name, kind="npm-script"))

    # ── tsconfig.json ─────────────────────────────────────────────────────────
    elif name.startswith("tsconfig") and isinstance(data, dict):
        tags.append("typescript-config")
        paths = (data.get("compilerOptions") or {}).get("paths") or {}
        for alias in paths:
            definitions.append(Definition(name=alias, kind="path-alias"))

    # ── Generic config / settings ─────────────────────────────────────────────
    elif ("config" in name or "settings" in name) and isinstance(data, dict):
        tags.append("config")
        for key in list(data.keys())[:30]:   # cap at 30 to avoid noise
            definitions.append(Definition(name=str(key), kind="config-key"))

    # ── JSON Schema ───────────────────────────────────────────────────────────
    elif isinstance(data, dict) and data.get("$schema"):
        tags.append("json-schema")

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
