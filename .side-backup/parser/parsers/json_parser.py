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
