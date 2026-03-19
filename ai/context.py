"""
ai/context.py
=============
Builds the AppContext passed to every tool call, and the initial
system message enriched with live project state.

The context gives the AI instant awareness of:
  - What project is loaded and its structure
  - Which file/node the user is currently looking at
  - Recent performance data (if .side-metrics.json exists)
  - Any errors or warnings from the last parse
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from typing import Any

from .client import ChatMessage
from .standards import get_system_prompt


# Tools available to every role. Role-specific subsets are applied at
# dispatch time when ctx.permitted_tools is non-empty.
ALL_TOOLS = frozenset([
    "read_file", "list_files", "get_file_summary", "search_definitions",
    "get_graph_overview", "get_metrics", "run_command",
    "get_definition_source", "write_file", "create_plan", "update_plan",
    "write_agent_note", "run_in_playground", "git", "profile_project",
    "write_session_file", "read_session_file", "list_session_files",
])

# Read-only tools — safe for any role
READ_TOOLS = frozenset([
    "read_file", "list_files", "get_file_summary", "search_definitions",
    "get_graph_overview", "get_metrics", "get_definition_source", "git",
    "read_session_file", "list_session_files",
])

# Default permitted tools per role
ROLE_TOOLS: dict[str, frozenset] = {
    "chat":           ALL_TOOLS,
    "architect":      READ_TOOLS | {"write_session_file", "create_plan",
                                     "update_plan", "write_agent_note"},
    "implementer":    ALL_TOOLS,
    "reviewer":       READ_TOOLS | {"write_session_file", "write_agent_note"},
    "tester":         READ_TOOLS | {"run_command", "run_in_playground",
                                     "profile_project",
                                     "write_session_file", "write_agent_note"},
    "optimizer":      ALL_TOOLS,  # includes profile_project
    "documentarian":  READ_TOOLS | {"write_session_file", "write_agent_note"},
}


@dataclass
class AppContext:
    """
    Live project state passed to every tool call.
    Constructed from the GUI's current state or a TeamSession.
    """
    project_root:    str  = ""
    project_name:    str  = ""
    graph:           dict | None = None   # full graph dict from last parse
    focused_node:    dict | None = None   # node the user last clicked
    focused_file:    str  = ""            # relative path of focused file
    metrics_path:    str  = ""            # path to .side-metrics.json if present
    # Agent team fields
    role:            str  = "chat"        # agent role (governs tool access)
    agent_name:      str  = ""            # display name for this agent
    session_root:    str  = ""            # .side/session/<session_id>/
    permitted_tools: frozenset = field(   # empty = use role default
        default_factory=frozenset)

    def can_use(self, tool_name: str) -> bool:
        """Return True if this context's role may call the named tool."""
        allowed = self.permitted_tools or ROLE_TOOLS.get(self.role, ALL_TOOLS)
        return tool_name in allowed

    @property
    def session_dir(self) -> str:
        """Absolute path to this session's scratch workspace."""
        if self.session_root:
            return self.session_root
        if self.project_root:
            return os.path.join(self.project_root, ".side", "session", "default")
        return ""


def build_context(
    project_root:  str,
    graph:         dict | None = None,
    focused_node:  dict | None = None,
    focused_file:  str = "",
    role:          str = "chat",
    agent_name:    str = "",
    session_root:  str = "",
) -> AppContext:
    """Construct an AppContext from GUI state or team session config."""
    name = ""
    if graph:
        name = graph.get("meta", {}).get("project", {}).get("name", "")
    metrics_path = os.path.join(project_root, ".side-metrics.json") if project_root else ""
    return AppContext(
        project_root=project_root,
        project_name=name or os.path.basename(project_root),
        graph=graph,
        focused_node=focused_node,
        focused_file=focused_file,
        metrics_path=metrics_path if os.path.isfile(metrics_path) else "",
        role=role,
        agent_name=agent_name,
        session_root=session_root,
    )


def build_system_message(ctx: AppContext, mode: str = "chat") -> ChatMessage:
    """
    Build the system ChatMessage for a new conversation.
    Includes the dev standards + a compact project summary.
    """
    system = get_system_prompt(mode)

    if ctx.project_root:
        system += f"\n\n## Current project: {ctx.project_name}"
        system += f"\nRoot: {ctx.project_root}"

    if ctx.graph:
        meta  = ctx.graph.get("meta", {})
        langs = meta.get("languages", {})
        lang_summary = ", ".join(
            f"{k} ({v['files']}f)" for k, v in sorted(langs.items())
        )
        system += f"\nFiles: {meta.get('totalFiles', '?')}  Edges: {meta.get('totalEdges', '?')}"
        if lang_summary:
            system += f"\nLanguages: {lang_summary}"
        docs = meta.get("docs", {})
        if not docs.get("healthy"):
            n = docs.get("summary", {}).get("total", 0)
            system += f"\nDoc warnings: {n} (README missing or stale)"
        perf = meta.get("perf", {})
        if perf.get("slowest"):
            system += f"\nSlowest parse stage: {perf['slowest']} ({perf.get('total_ms', '?')}ms total)"

    if ctx.focused_file:
        system += f"\n\nUser is looking at: {ctx.focused_file}"
        # Find node data for the focused file
        if ctx.graph:
            node = next(
                (n for n in ctx.graph.get("nodes", [])
                 if n.get("path") == ctx.focused_file),
                None,
            )
            if node:
                defs = node.get("definitions", [])
                if defs:
                    fn_names = [d.get("name") for d in defs[:8]]
                    system += f"\nFunctions in this file: {', '.join(fn_names)}"
                    high_complexity = [
                        d for d in defs
                        if d.get("complexity", 0) > 7
                    ]
                    if high_complexity:
                        system += f"\nHigh-complexity functions: {[d['name'] for d in high_complexity]}"

    if ctx.metrics_path:
        try:
            data = json.load(open(ctx.metrics_path))
            files = data.get("files", {})
            if files:
                slowest = sorted(files.items(), key=lambda x: -x[1].get("avg_ms", 0))[:3]
                summary = ", ".join(f"{os.path.basename(k)} {v['avg_ms']:.0f}ms" for k, v in slowest)
                system += f"\nLive metrics active — slowest files: {summary}"
        except Exception:
            pass

    # Role and session context
    if ctx.role and ctx.role != "chat":
        system += f"\n\n## Your role: {ctx.role.upper()}"
        if ctx.agent_name:
            system += f" ({ctx.agent_name})"
            allowed = ctx.permitted_tools or ROLE_TOOLS.get(ctx.role, ALL_TOOLS)
        system += f"\nYou may use these tools: {', '.join(sorted(allowed))}"
        if "write_file" not in allowed:
            system += (
                "\n\nIMPORTANT: You cannot write to project source files directly. "
                "Use write_session_file to write reports, notes, and documentation "
                "drafts to the session workspace. The Architect or a human will "
                "promote approved outputs to the real project tree."
            )
        if ctx.session_dir:
            system += f"\nSession workspace: {ctx.session_dir}"
            system += (
                "\nUse write_session_file(path, content) to save your work. "
                "Use list_session_files() to see what prior agents wrote. "
                "Use read_session_file(path) to read them."
            )

    return ChatMessage(role="system", content=system)


def summarise_graph_for_prompt(graph: dict, max_files: int = 20) -> str:
    """
    Return a compact text summary of the project graph suitable for
    embedding in a prompt without blowing the context window.
    """
    if not graph:
        return "(no project loaded)"
    meta  = graph.get("meta", {})
    nodes = graph.get("nodes", [])
    lines = [
        f"Project: {meta.get('project', {}).get('name', '?')}",
        f"Files: {len(nodes)}, Edges: {len(graph.get('edges', []))}",
    ]
    # Top files by line count
    top = sorted(nodes, key=lambda n: -n.get("lines", 0))[:max_files]
    lines.append("\nFiles (by size):")
    for n in top:
        defs = len(n.get("definitions", []))
        lines.append(f"  {n['path']:40s}  {n.get('lines',0):5d}L  {defs}defs")
    return "\n".join(lines)
