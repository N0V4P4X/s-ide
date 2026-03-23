# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
parser/project_config.py
========================
Reads, writes, and initialises side.project.json for a project.

side.project.json schema
------------------------
{
  "name":        "my-project",
  "version":     "0.1.0",
  "description": "",
  "ignore":      ["dist", "*.test.py"],
  "run": {
    "dev":   "python main.py",
    "test":  "pytest"
  },
  "versions": {
    "dir":      "versions",
    "compress": true,
    "keep":     20
  },
  "meta": {}
}

All keys are optional; missing keys are filled in from DEFAULTS.
The file is created automatically on first parse of a new project.
"""

from __future__ import annotations
import json
import os
from copy import deepcopy
from typing import Any

CONFIG_FILE = "side.project.json"

DEFAULTS: dict[str, Any] = {
    "name":        None,   # inferred from directory name when None
    "version":     "0.1.0",
    "description": "",
    "ignore":      [],
    "run":         {},
    "versions": {
        "dir":      "versions",
        "compress": True,
        "keep":     20,
    },
    "meta": {},
}


def load_project_config(root_dir: str) -> dict:
    """
    Load side.project.json from root_dir.
    Returns a fully-merged config dict with DEFAULTS applied.
    Internal keys (_path, _exists, _error) are added but never written back.
    """
    config_path = os.path.join(root_dir, CONFIG_FILE)
    base = deepcopy(DEFAULTS)
    base["name"] = os.path.basename(root_dir)

    if not os.path.exists(config_path):
        base["_path"] = config_path
        base["_exists"] = False
        return base

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # Deep merge: top-level keys override defaults; nested dicts merged
        merged = {**base, **raw}
        merged["versions"] = {**DEFAULTS["versions"], **raw.get("versions", {})}
        merged["run"] = {**DEFAULTS["run"], **raw.get("run", {})}
        merged["_path"] = config_path
        merged["_exists"] = True
        return merged
    except Exception as exc:
        base["_path"] = config_path
        base["_exists"] = False
        base["_error"] = str(exc)
        return base


def save_project_config(root_dir: str, config: dict) -> str:
    """
    Write config back to side.project.json, stripping internal _ keys.
    Returns the path written.
    """
    config_path = os.path.join(root_dir, CONFIG_FILE)
    clean = {k: v for k, v in config.items() if not k.startswith("_")}
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2)
    return config_path


def init_project_config(root_dir: str) -> dict:
    """
    Load config if it exists; otherwise create a sensible default
    side.project.json and return the resulting config.

    Also attempts to read version from pyproject.toml or package.json
    so newly-added projects start with the right version number.
    """
    config_path = os.path.join(root_dir, CONFIG_FILE)
    if os.path.exists(config_path):
        return load_project_config(root_dir)

    name = os.path.basename(root_dir)
    version = "0.1.0"

    # Try pyproject.toml
    try:
        import re
        pyproject = os.path.join(root_dir, "pyproject.toml")
        if os.path.isfile(pyproject):
            with open(pyproject, encoding="UTF-8") as f:
                text = f.read()
            m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
            if m:
                version = m.group(1)
    except Exception:
        pass

    # Try package.json
    if version == "0.1.0":
        try:
            pkg = os.path.join(root_dir, "package.json")
            if os.path.isfile(pkg):
                with open(pkg, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("version"):
                    version = data["version"]
        except Exception:
            pass

    config = {
        "name":        name,
        "version":     version,
        "description": "",
        "ignore":      [],
        "run":         {},
        "versions":    {"dir": "versions", "compress": True, "keep": 20},
        "meta":        {},
    }
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    config["_path"] = config_path
    config["_exists"] = True
    config["_created"] = True
    return config


def bump_version(current: str, part: str = "patch") -> str:
    """
    Increment a semver string.
    part: 'major' | 'minor' | 'patch'  (default: 'patch')
    """
    parts = [int(x) for x in str(current or "0.0.0").split(".")]
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts[0], parts[1], parts[2]
    if part == "major":
        major += 1; minor = 0; patch = 0
    elif part == "minor":
        minor += 1; patch = 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"

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
