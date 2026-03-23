# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
ai/tool_builder.py
==================
Self-improving tool creation workflow.

When the Manager calls a tool that doesn't exist, instead of silently
failing, it enters a tool-building workflow:

  1. Manager calls unknown_tool(args)
  2. dispatch detects "Unknown tool: X"
  3. ToolMissingError raised with the tool name + args + model's intent
  4. Manager pauses, calls on_tool_missing(spec)
  5. GUI shows approval dialog: "Build this tool?"
  6. On approval: TeamSession builds the tool (architect+implementer+tester)
  7. Tool registered in _CUSTOM_HANDLERS + TOOLS schema
  8. Manager resumes its original turn with the new tool available

The new tool is written to:
  .side/tools/<tool_name>.py

And registered at runtime so future sessions also have it.

Tool contract
-------------
Every custom tool module must expose:

    TOOL_SCHEMA: dict   — the Ollama-compatible schema (type/function/name/…)
    TOOL_HANDLER: callable  — handler(args: dict, ctx: AppContext) -> any

These are imported and registered by register_custom_tool().
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ── Custom tool registry ───────────────────────────────────────────────────────

# Runtime registry: tool_name → (schema_dict, handler_fn)
_CUSTOM_REGISTRY: dict[str, tuple[dict, Callable]] = {}


def register_custom_tool(tool_path: str) -> str:
    """
    Load a custom tool from a .py file and register it.

    The file must define TOOL_SCHEMA (dict) and TOOL_HANDLER (callable).

    Args:
        tool_path: Absolute path to the tool .py file.

    Returns:
        The tool name as registered.

    Raises:
        ValueError: If the file is missing TOOL_SCHEMA or TOOL_HANDLER.
        ImportError: If the file cannot be loaded.
    """
    name = os.path.splitext(os.path.basename(tool_path))[0]
    spec = importlib.util.spec_from_file_location(f"side_tool_{name}", tool_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {tool_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    schema  = getattr(mod, "TOOL_SCHEMA", None)
    handler = getattr(mod, "TOOL_HANDLER", None)
    if schema is None:
        raise ValueError(f"{tool_path}: missing TOOL_SCHEMA")
    if handler is None:
        raise ValueError(f"{tool_path}: missing TOOL_HANDLER")

    tool_name = schema.get("function", {}).get("name", name)
    _CUSTOM_REGISTRY[tool_name] = (schema, handler)
    return tool_name


def load_all_custom_tools(project_root: str) -> list[str]:
    """
    Load every custom tool from .side/tools/*.py in the project.

    Args:
        project_root: Project directory to scan.

    Returns:
        List of tool names registered.
    """
    tools_dir = os.path.join(project_root, ".side", "tools")
    if not os.path.isdir(tools_dir):
        return []
    registered = []
    for fname in sorted(os.listdir(tools_dir)):
        if fname.endswith(".py") and not fname.startswith("_"):
            try:
                name = register_custom_tool(os.path.join(tools_dir, fname))
                registered.append(name)
            except Exception as e:
                print(f"[tool_builder] Failed to load {fname}: {e}", file=sys.stderr)
    return registered


def get_custom_schemas() -> list[dict]:
    """Return all registered custom tool schemas for inclusion in TOOLS."""
    return [schema for schema, _ in _CUSTOM_REGISTRY.values()]


def dispatch_custom(name: str, args: dict, ctx: Any) -> Any:
    """
    Dispatch a custom tool call.

    Returns None if the tool is not in the custom registry.
    """
    entry = _CUSTOM_REGISTRY.get(name)
    if entry is None:
        return None
    _, handler = entry
    return handler(args, ctx)


def is_custom_tool(name: str) -> bool:
    """Return True if name is a registered custom tool."""
    return name in _CUSTOM_REGISTRY


# ── Missing tool detection ────────────────────────────────────────────────────

@dataclass
class ToolMissingError(Exception):
    """
    Raised when the model calls a tool that does not exist.

    Carries enough information to create a plan for building it.
    """
    tool_name:  str
    tool_args:  dict                  # args the model tried to pass
    intent:     str = ""              # model's surrounding text (why it wanted this)
    suggestions: list[str] = field(default_factory=list)  # related existing tools


@dataclass
class ToolBuildSpec:
    """
    Specification for building a new tool, shown to the user for approval.

    Created by infer_tool_spec() from a ToolMissingError.
    """
    tool_name:    str
    description:  str
    args_schema:  dict   # {param_name: {"type": ..., "description": ...}}
    returns:      str    # description of return value
    intent:       str    # why the model wanted it
    file_path:    str    # where it will be written

    def summary(self) -> str:
        lines = [
            f"Tool: {self.tool_name}",
            f"Description: {self.description}",
            f"Args: {', '.join(self.args_schema.keys()) or 'none'}",
            f"Returns: {self.returns}",
            f"File: {self.file_path}",
        ]
        return "\n".join(lines)

    def to_team_task(self) -> str:
        """Generate a precise task description for the tool-building team."""
        args_desc = "\n".join(
            f"  - {k}: {v.get('type','any')} — {v.get('description','')}"
            for k, v in self.args_schema.items()
        ) or "  (no arguments)"
        return (
            f"Build a new S-IDE tool called `{self.tool_name}`.\n\n"
            f"## Why it's needed\n{self.intent}\n\n"
            f"## Specification\n"
            f"Description: {self.description}\n\n"
            f"Arguments:\n{args_desc}\n\n"
            f"Returns: {self.returns}\n\n"
            f"## Output file\nWrite the tool to: {self.file_path}\n\n"
            f"## Required structure\n"
            f"The file must define exactly two module-level names:\n\n"
            f"```python\n"
            f"TOOL_SCHEMA = {{\n"
            f"    'type': 'function',\n"
            f"    'function': {{\n"
            f"        'name': '{self.tool_name}',\n"
            f"        'description': '...',\n"
            f"        'parameters': {{'type': 'object', 'properties': {{...}}, 'required': [...]}}\n"
            f"    }}\n"
            f"}}\n\n"
            f"def TOOL_HANDLER(args: dict, ctx) -> dict:\n"
            f"    # Implementation here\n"
            f"    ...\n"
            f"```\n\n"
            f"## Constraints\n"
            f"- Pure Python, stdlib only unless the project already depends on something\n"
            f"- Handler must return a JSON-serialisable dict\n"
            f"- Must have a test in .side/tools/test_{self.tool_name}.py\n"
            f"- Test must pass before the tool is considered complete\n"
        )


def infer_tool_spec(error: ToolMissingError, project_root: str) -> ToolBuildSpec:
    """
    Build a ToolBuildSpec from what the model tried to call.

    Infers argument types from the values the model passed, generates
    a description from the tool name and intent.
    """
    # Infer arg schema from the values the model tried to pass
    args_schema: dict[str, dict] = {}
    for k, v in error.tool_args.items():
        if isinstance(v, bool):
            t = "boolean"
        elif isinstance(v, int):
            t = "integer"
        elif isinstance(v, float):
            t = "number"
        elif isinstance(v, list):
            t = "array"
        elif isinstance(v, dict):
            t = "object"
        else:
            t = "string"
        args_schema[k] = {"type": t, "description": f"The {k.replace('_', ' ')}"}

    # Human-readable description from the tool name
    words = error.tool_name.replace("_", " ").split()
    description = " ".join(words).capitalize()

    file_path = os.path.join(
        project_root, ".side", "tools", f"{error.tool_name}.py"
    )

    return ToolBuildSpec(
        tool_name   = error.tool_name,
        description = description,
        args_schema = args_schema,
        returns     = "dict with result data",
        intent      = error.intent or f"Model tried to call {error.tool_name}({error.args})",
        file_path   = file_path,
    )


# ── Tool build team workflow ───────────────────────────────────────────────────

def build_tool_with_team(
    spec:         ToolBuildSpec,
    project_root: str,
    graph:        Optional[dict],
    model:        str,
    on_event:     Callable,
) -> Optional[str]:
    """
    Run a minimal TeamSession to build and test a new tool.

    Uses architect + implementer + tester. Returns the path to the
    built tool file if successful, None on failure.

    Args:
        spec:         What to build.
        project_root: Project root directory.
        graph:        Current project graph (for context).
        model:        Ollama model name.
        on_event:     TeamEvent callback.

    Returns:
        Path to the written tool file, or None on failure.
    """
    from ai.teams import TeamSession, AgentConfig

    # Ensure output directory exists
    os.makedirs(os.path.join(project_root, ".side", "tools"), exist_ok=True)

    session = TeamSession(
        project_root = project_root,
        task         = spec.to_team_task(),
        agents       = [
            AgentConfig(role="architect",   model=model, name="ToolArchitect",   max_rounds=4),
            AgentConfig(role="implementer", model=model, name="ToolBuilder",     max_rounds=8),
            AgentConfig(role="tester",      model=model, name="ToolVerifier",    max_rounds=4),
        ],
        graph    = graph,
        on_event = on_event,
    )

    result = session.run()

    # Check if the file was written
    if os.path.isfile(spec.file_path):
        return spec.file_path

    # Check session workspace for the file
    for turn in result.turns:
        for sf in turn.session_files:
            if spec.tool_name in sf and sf.endswith(".py"):
                full = os.path.join(result.session_dir, sf)
                if os.path.isfile(full):
                    # Promote it
                    import shutil
                    os.makedirs(os.path.dirname(spec.file_path), exist_ok=True)
                    shutil.copy2(full, spec.file_path)
                    return spec.file_path

    return None

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
