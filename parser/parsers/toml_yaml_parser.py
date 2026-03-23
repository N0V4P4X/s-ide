# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
parser/parsers/toml_yaml_parser.py
====================================
Extracts semantic structure from TOML and YAML config files.

Handles (without third-party dependencies):

TOML (stdlib tomllib on Python 3.11+, regex fallback on older):
  pyproject.toml  → project name/version, dependencies, scripts, tool configs
  Cargo.toml      → name/version, dependencies
  *.toml          → generic key extraction

YAML (regex-based — no PyYAML required):
  docker-compose.yml  → services, images, ports
  .github/workflows/  → job names, steps
  *.yml / *.yaml      → generic key extraction

Returns the same {imports, exports, definitions, tags, errors} shape
as every other parser so the orchestrator treats them uniformly.
"""

from __future__ import annotations
import os
import re
from graph.types import ImportRecord, ExportRecord, Definition


# ── TOML parsing ─────────────────────────────────────────────────────────────

def _try_stdlib_toml(source: str) -> dict | None:
    """Use stdlib tomllib (Python 3.11+) if available."""
    try:
        import tomllib
        return tomllib.loads(source)
    except ImportError:
        return None
    except Exception:
        return None


def _regex_toml_key(source: str, key: str) -> str | None:
    """Extract a simple string value from TOML by key name."""
    m = re.search(
        rf'^{re.escape(key)}\s*=\s*["\']([^"\']+)["\']',
        source, re.MULTILINE
    )
    return m.group(1) if m else None


def _regex_toml_array(source: str, key: str) -> list[str]:
    """Extract a flat string array from TOML: key = ["a", "b", ...]"""
    m = re.search(
        rf'^{re.escape(key)}\s*=\s*\[([^\]]*)\]',
        source, re.MULTILINE | re.DOTALL
    )
    if not m:
        return []
    return re.findall(r'["\']([^"\']+)["\']', m.group(1))


def _parse_pyproject_toml(data: dict | None, source: str) -> dict:
    """Extract from pyproject.toml using parsed data or regex fallback."""
    imports:     list[ImportRecord] = []
    exports:     list[ExportRecord] = []
    definitions: list[Definition]   = []
    tags:        list[str]          = ["pyproject"]

    if data:
        project = data.get("project") or data.get("tool", {}).get("poetry", {})
        name    = project.get("name", "")
        version = project.get("version", "")

        if name:
            tags.append(f"pkg:{name}")
        if version:
            definitions.append(Definition(name="version", kind="config-key"))

        # PEP 508 dependencies
        for dep in (project.get("dependencies") or []):
            if isinstance(dep, str):
                pkg = re.split(r"[>=<!;\[]", dep)[0].strip()
                if pkg:
                    imports.append(ImportRecord(type="pyproject-dep", source=pkg))
            elif isinstance(dep, dict):
                for pkg in dep:
                    imports.append(ImportRecord(type="pyproject-dep", source=pkg))

        # Optional/dev dependencies
        for group in (project.get("optional-dependencies") or {}).values():
            for dep in group:
                if isinstance(dep, str):
                    pkg = re.split(r"[>=<!;\[]", dep)[0].strip()
                    if pkg:
                        imports.append(ImportRecord(type="pyproject-dev-dep", source=pkg))

        # Poetry-style dependencies
        for pkg, val in (data.get("tool", {}).get("poetry", {}).get("dependencies") or {}).items():
            if pkg.lower() != "python":
                imports.append(ImportRecord(type="poetry-dep", source=pkg))

        # Scripts
        for script_name in (project.get("scripts") or {}):
            definitions.append(Definition(name=script_name, kind="entry-point"))

        # Detect tools present
        tool_cfg = data.get("tool") or {}
        for tool in ("pytest", "black", "ruff", "mypy", "isort", "flake8", "coverage"):
            if tool in tool_cfg:
                tags.append(f"tool:{tool}")

        if data.get("build-system"):
            tags.append("build-system")

    else:
        # Regex fallback
        name = _regex_toml_key(source, "name")
        if name:
            tags.append(f"pkg:{name}")
        for dep in _regex_toml_array(source, "dependencies"):
            pkg = re.split(r"[>=<!;\[]", dep)[0].strip()
            if pkg:
                imports.append(ImportRecord(type="pyproject-dep", source=pkg))

    return {"imports": imports, "exports": exports,
            "definitions": definitions, "tags": tags, "errors": []}


def _parse_cargo_toml(data: dict | None, source: str) -> dict:
    """Extract from Cargo.toml (Rust)."""
    imports:     list[ImportRecord] = []
    definitions: list[Definition]   = []
    tags = ["cargo"]

    if data:
        pkg = data.get("package") or {}
        if pkg.get("name"):
            tags.append(f"pkg:{pkg['name']}")
        for dep_name in (data.get("dependencies") or {}):
            imports.append(ImportRecord(type="cargo-dep", source=dep_name))
        for dep_name in (data.get("dev-dependencies") or {}):
            imports.append(ImportRecord(type="cargo-dev-dep", source=dep_name))
    else:
        for dep in _regex_toml_array(source, "dependencies"):
            imports.append(ImportRecord(type="cargo-dep", source=dep))

    return {"imports": imports, "exports": [], "definitions": definitions,
            "tags": tags, "errors": []}


def _parse_generic_toml(data: dict | None, source: str) -> dict:
    """Generic TOML: extract top-level keys as config definitions."""
    definitions: list[Definition] = []
    tags = ["config", "toml"]

    if data:
        for key in list(data.keys())[:30]:
            definitions.append(Definition(name=str(key), kind="config-key"))
    else:
        for m in re.finditer(r'^(\w[\w.-]*)\s*=', source, re.MULTILINE):
            definitions.append(Definition(name=m.group(1), kind="config-key"))

    return {"imports": [], "exports": [], "definitions": definitions,
            "tags": tags, "errors": []}


def parse_toml(source: str, file_path: str = "") -> dict:
    """
    Parse a TOML file and return semantic records.
    Dispatches to specialised handlers for pyproject.toml and Cargo.toml.
    """
    name = os.path.basename(file_path).lower()
    data = _try_stdlib_toml(source)

    if name == "pyproject.toml":
        return _parse_pyproject_toml(data, source)
    if name == "cargo.toml":
        return _parse_cargo_toml(data, source)
    return _parse_generic_toml(data, source)


# ── YAML parsing (regex — no PyYAML) ─────────────────────────────────────────

def _yaml_top_keys(source: str) -> list[str]:
    """Extract top-level keys from YAML (lines starting at column 0)."""
    keys = []
    for m in re.finditer(r'^([a-zA-Z_][\w-]*)\s*:', source, re.MULTILINE):
        keys.append(m.group(1))
    return keys


def _parse_docker_compose(source: str) -> dict:
    """Extract services, images, and ports from docker-compose.yml."""
    imports:     list[ImportRecord] = []
    definitions: list[Definition]   = []
    tags = ["docker-compose"]

    # Services: lines like "  servicename:" at 2-space indent
    for m in re.finditer(r'^  ([a-zA-Z][\w-]+)\s*:', source, re.MULTILINE):
        service = m.group(1)
        if service not in ("version", "services", "networks", "volumes", "configs", "secrets"):
            definitions.append(Definition(name=service, kind="docker-service"))

    # Images used
    for m in re.finditer(r'^\s+image:\s*([^\s#]+)', source, re.MULTILINE):
        img = m.group(1).strip().strip('"\'')
        base = img.split(":")[0]
        imports.append(ImportRecord(type="docker-image", source=base))

    return {"imports": imports, "exports": [],
            "definitions": definitions, "tags": tags, "errors": []}


def _parse_github_workflow(source: str) -> dict:
    """Extract job names and key actions from GitHub Actions workflow files."""
    definitions: list[Definition] = []
    imports:     list[ImportRecord] = []
    tags = ["github-actions"]

    # Jobs section
    in_jobs = False
    for line in source.splitlines():
        if re.match(r'^jobs\s*:', line):
            in_jobs = True
            continue
        if in_jobs and re.match(r'^  ([a-zA-Z][\w-]+)\s*:', line):
            m = re.match(r'^  ([a-zA-Z][\w-]+)\s*:', line)
            definitions.append(Definition(name=m.group(1), kind="workflow-job"))
        elif in_jobs and not line.startswith(" ") and line.strip() and ":" in line:
            in_jobs = False

    # Uses (actions)
    for m in re.finditer(r'uses:\s*([^\s#]+)', source):
        action = m.group(1).strip().strip('"\'')
        if "/" in action:
            imports.append(ImportRecord(type="github-action", source=action))

    return {"imports": imports, "exports": [],
            "definitions": definitions, "tags": tags, "errors": []}


def _parse_generic_yaml(source: str) -> dict:
    """Generic YAML: extract top-level keys."""
    definitions = [
        Definition(name=k, kind="config-key")
        for k in _yaml_top_keys(source)[:30]
    ]
    return {"imports": [], "exports": [], "definitions": definitions,
            "tags": ["config", "yaml"], "errors": []}


def parse_yaml(source: str, file_path: str = "") -> dict:
    """
    Parse a YAML file and return semantic records.
    Dispatches to specialised handlers for docker-compose and GitHub Actions.
    """
    name      = os.path.basename(file_path).lower()
    path_norm = file_path.replace("\\", "/")

    # Docker Compose
    if name in ("docker-compose.yml", "docker-compose.yaml") or \
       re.search(r"docker.compose", name):
        return _parse_docker_compose(source)

    # GitHub Actions workflow
    if ".github/workflows" in path_norm or ".github/actions" in path_norm:
        return _parse_github_workflow(source)

    return _parse_generic_yaml(source)

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
