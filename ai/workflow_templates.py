# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
ai/workflow_templates.py
========================
Saved workflow templates for the AI Teams canvas.

A template is a named, reusable workflow configuration: a sequence of
agent roles with their models, edges, and a description. Templates are
stored as JSON in ~/.s-ide-templates.json so they persist across projects
and sessions.

Built-in templates are provided for common workflows. Users can save
their own from the Teams canvas and load them later.

Usage
-----
    from ai.workflow_templates import (
        list_templates, get_template, save_template,
        delete_template, BUILTIN_TEMPLATES,
    )

    # Load a built-in
    t = get_template("standard_review")
    nodes, edges = t.to_canvas_nodes()

    # Save current canvas as a template
    save_template("my_workflow", nodes, edges, description="My custom flow")

    # List all available
    for t in list_templates():
        print(t.name, "--", t.description)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class AgentTemplate:
    """One agent slot in a workflow template."""
    role:  str
    model: str = "llama3.2"
    name:  str = ""

    def __post_init__(self):
        if not self.name:
            self.name = self.role.title()


@dataclass
class WorkflowTemplate:
    """
    A named, reusable workflow configuration.

    Stores agents in sequence order — edges are implied by adjacency.
    Custom edge topologies can be stored in the `edges` list.
    """
    name:        str
    description: str
    agents:      list[AgentTemplate] = field(default_factory=list)
    # Optional: explicit edges for non-linear workflows
    edges:       list[dict] = field(default_factory=list)
    builtin:     bool = False

    def to_canvas_nodes(self, start_x: float = 80.0, start_y: float = 80.0,
                         gap: float = 260.0) -> tuple[list[dict], list[dict]]:
        """
        Convert to (nodes, edges) for the Teams canvas.

        Returns node dicts with x/y positions and edge dicts connecting
        them in sequence.
        """
        nodes = []
        edges = []
        prev_id = None

        for i, agent in enumerate(self.agents):
            nid = f"tw_tpl_{i+1}"
            nodes.append({
                "id":    nid,
                "role":  agent.role,
                "model": agent.model,
                "name":  agent.name,
                "x":     start_x + i * gap,
                "y":     start_y,
            })
            if prev_id:
                edges.append({
                    "id":     f"te_{prev_id}_{nid}",
                    "source": prev_id,
                    "target": nid,
                })
            prev_id = nid

        # Overlay explicit edges if provided
        if self.edges:
            edges = self.edges

        return nodes, edges

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "description": self.description,
            "agents":      [{"role": a.role, "model": a.model, "name": a.name}
                            for a in self.agents],
            "edges":       self.edges,
            "builtin":     self.builtin,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WorkflowTemplate":
        return cls(
            name        = d.get("name", ""),
            description = d.get("description", ""),
            agents      = [AgentTemplate(**a) for a in d.get("agents", [])],
            edges       = d.get("edges", []),
            builtin     = d.get("builtin", False),
        )


# ── Built-in templates ────────────────────────────────────────────────────────

BUILTIN_TEMPLATES: dict[str, WorkflowTemplate] = {
    "standard_review": WorkflowTemplate(
        name="standard_review",
        description="Architect → Implementer → Reviewer → Tester",
        builtin=True,
        agents=[
            AgentTemplate("architect",   "llama3.2"),
            AgentTemplate("implementer", "llama3.2"),
            AgentTemplate("reviewer",    "llama3.2"),
            AgentTemplate("tester",      "llama3.2"),
        ],
    ),
    "quick_implement": WorkflowTemplate(
        name="quick_implement",
        description="Implementer → Tester (fast, no formal review)",
        builtin=True,
        agents=[
            AgentTemplate("implementer", "llama3.2"),
            AgentTemplate("tester",      "llama3.2"),
        ],
    ),
    "full_pipeline": WorkflowTemplate(
        name="full_pipeline",
        description="Full 6-agent pipeline: Architect → Implementer → Reviewer → Tester → Optimizer → Documentarian",
        builtin=True,
        agents=[
            AgentTemplate("architect",    "llama3.2"),
            AgentTemplate("implementer",  "llama3.2"),
            AgentTemplate("reviewer",     "llama3.2"),
            AgentTemplate("tester",       "llama3.2"),
            AgentTemplate("optimizer",    "llama3.2"),
            AgentTemplate("documentarian","llama3.2"),
        ],
    ),
    "optimize_only": WorkflowTemplate(
        name="optimize_only",
        description="Profile → Optimize → Verify",
        builtin=True,
        agents=[
            AgentTemplate("tester",    "llama3.2", name="Profiler"),
            AgentTemplate("optimizer", "llama3.2"),
            AgentTemplate("tester",    "llama3.2", name="Verifier"),
        ],
    ),
    "docs_update": WorkflowTemplate(
        name="docs_update",
        description="Reviewer → Documentarian (read-only review then doc update)",
        builtin=True,
        agents=[
            AgentTemplate("reviewer",      "llama3.2"),
            AgentTemplate("documentarian", "llama3.2"),
        ],
    ),
}


# ── Persistence ────────────────────────────────────────────────────────────────

_TEMPLATES_PATH = os.path.expanduser("~/.s-ide-templates.json")


def _load_user_templates() -> dict[str, WorkflowTemplate]:
    """Load user-saved templates from ~/.s-ide-templates.json."""
    if not os.path.isfile(_TEMPLATES_PATH):
        return {}
    try:
        data = json.load(open(_TEMPLATES_PATH, encoding="utf-8"))
        return {
            name: WorkflowTemplate.from_dict(d)
            for name, d in data.items()
        }
    except Exception:
        return {}


def _save_user_templates(templates: dict[str, WorkflowTemplate]) -> None:
    """Write user templates to disk."""
    try:
        data = {name: t.to_dict() for name, t in templates.items()
                if not t.builtin}
        with open(_TEMPLATES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


# ── Public API ─────────────────────────────────────────────────────────────────

def list_templates() -> list[WorkflowTemplate]:
    """
    Return all available templates: built-ins first, then user-saved.

    Returns:
        List of WorkflowTemplate objects sorted by name.
    """
    all_t = dict(BUILTIN_TEMPLATES)
    all_t.update(_load_user_templates())
    return sorted(all_t.values(), key=lambda t: (not t.builtin, t.name))


def get_template(name: str) -> Optional[WorkflowTemplate]:
    """
    Get a template by name.

    Checks built-ins first, then user-saved templates.

    Args:
        name: Template name.

    Returns:
        WorkflowTemplate or None if not found.
    """
    if name in BUILTIN_TEMPLATES:
        return BUILTIN_TEMPLATES[name]
    user = _load_user_templates()
    return user.get(name)


def save_template(
    name:        str,
    nodes:       list[dict],
    edges:       list[dict],
    description: str = "",
    model:       str = "llama3.2",
) -> WorkflowTemplate:
    """
    Save the current Teams canvas layout as a named template.

    Args:
        name:        Unique template name (slug, no spaces).
        nodes:       Canvas node dicts (id, role, model, name, x, y).
        edges:       Canvas edge dicts (id, source, target).
        description: Human-readable description.
        model:       Default model (used if node has no model set).

    Returns:
        The saved WorkflowTemplate.
    """
    agents = [
        AgentTemplate(
            role  = n.get("role", "implementer"),
            model = n.get("model", model),
            name  = n.get("name", ""),
        )
        for n in sorted(nodes, key=lambda n: n.get("x", 0))
    ]
    t = WorkflowTemplate(
        name        = name,
        description = description or f"Custom: {' → '.join(a.role for a in agents)}",
        agents      = agents,
        edges       = edges,
        builtin     = False,
    )
    user = _load_user_templates()
    user[name] = t
    _save_user_templates(user)
    return t


def delete_template(name: str) -> bool:
    """
    Delete a user-saved template. Cannot delete built-ins.

    Args:
        name: Template name to delete.

    Returns:
        True if deleted, False if not found or is a built-in.
    """
    if name in BUILTIN_TEMPLATES:
        return False
    user = _load_user_templates()
    if name not in user:
        return False
    del user[name]
    _save_user_templates(user)
    return True


def template_to_agent_configs(template: WorkflowTemplate) -> list[dict]:
    """
    Convert a template to a list of AgentConfig-compatible dicts
    suitable for passing to TeamSession.

    Returns:
        List of {"role": ..., "model": ..., "name": ...} dicts.
    """
    return [
        {"role": a.role, "model": a.model, "name": a.name}
        for a in template.agents
    ]

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
