# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
ai/teams.py
===========
Turn-based multi-agent development workflow engine.

A TeamSession orchestrates a sequence of AI agents, each with a defined role,
running in the same project context. Agents communicate through a shared
session workspace (.side/session/<id>/) and structured handoff notes.

Design
------
- Each agent runs in a sandboxed copy of the project (via build/sandbox.py)
  so no agent can corrupt the working tree until the human approves.
- Agents take turns: one runs to completion, writes its outputs to the
  session workspace, then the next agent starts.
- The session workspace persists across the full workflow so every agent
  can read what prior agents produced.
- The human reviews and approves (or rejects) the final output before
  anything is applied to the real project.

Usage
-----
    from ai.teams import TeamSession, AgentConfig, WorkflowResult

    session = TeamSession(
        project_root="/path/to/project",
        task="Add input validation to the parse_file function",
        agents=[
            AgentConfig(role="architect", model="llama3.2"),
            AgentConfig(role="implementer", model="codellama"),
            AgentConfig(role="reviewer", model="llama3.2"),
            AgentConfig(role="tester", model="llama3.2"),
        ],
        on_event=lambda e: print(f"[{e.agent}] {e.type}: {e.message}"),
    )

    result = session.run()       # blocking
    # or
    session.run_async(callback)  # non-blocking

    if result.approved:
        result.apply()           # copy session outputs to project
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from ai.client import OllamaClient, ChatMessage
from ai.context import AppContext, build_context, build_system_message, ROLE_TOOLS
from ai.roles import get_role_prompt
from ai.tools import TOOLS, dispatch_tool


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class AgentConfig:
    """Configuration for one agent in a team workflow."""
    role:       str                    # architect | implementer | reviewer | tester | …
    model:      str = "llama3.2"      # Ollama model name
    name:       str = ""              # display name (defaults to role title)
    max_rounds: int = 8               # max tool-calling rounds before forced stop
    # Optional: override which tools this agent may use
    permitted_tools: frozenset = field(default_factory=frozenset)

    def __post_init__(self):
        if not self.name:
            self.name = self.role.title()


@dataclass
class TeamEvent:
    """An event emitted during a workflow run — for UI progress display."""
    type:    str      # "start" | "text" | "tool" | "tool_result" | "done" | "error" | "handoff"
    agent:   str      # agent name
    role:    str
    message: str
    data:    dict = field(default_factory=dict)


@dataclass
class AgentTurn:
    """The complete record of one agent's turn."""
    agent:    str
    role:     str
    model:    str
    response: str           # full text response
    tool_calls: list        # list of {name, args, result} dicts
    session_files: list     # files written to session workspace
    error:    str = ""      # non-empty if the turn failed
    duration_s: float = 0.0


@dataclass
class WorkflowResult:
    """The outcome of a complete team workflow run."""
    session_id:    str
    task:          str
    project_root:  str
    session_dir:   str      # .side/session/<id>/
    turns:         list[AgentTurn] = field(default_factory=list)
    verdict:       str = "pending"    # pending | approved | rejected
    error:         str = ""

    def summary(self) -> str:
        lines = [f"Session {self.session_id[:8]}: {self.task[:60]}"]
        for t in self.turns:
            status = "✓" if not t.error else "✗"
            lines.append(f"  {status} {t.agent} ({t.role}) — {len(t.tool_calls)} tools, "
                         f"{len(t.session_files)} files, {t.duration_s:.1f}s")
        lines.append(f"  Verdict: {self.verdict}")
        return "\n".join(lines)

    def apply(self, target_dir: Optional[str] = None) -> list[str]:
        """
        Promote session files to the project (or target_dir).
        Only promotes files from session/implementation/ and session/docs/
        (not review reports or test results).
        Returns list of files applied.
        """
        target = target_dir or self.project_root
        applied = []
        promote_dirs = ["implementation/", "docs/"]
        for d in promote_dirs:
            src_dir = os.path.join(self.session_dir, d)
            if not os.path.isdir(src_dir):
                continue
            for root, _, files in os.walk(src_dir):
                for f in files:
                    src_path = os.path.join(root, f)
                    rel = os.path.relpath(src_path, src_dir)
                    dst_path = os.path.join(target, rel)
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    shutil.copy2(src_path, dst_path)
                    applied.append(rel)
        self.verdict = "approved"
        return applied


# ── Team session ──────────────────────────────────────────────────────────────

class TeamSession:
    """
    Orchestrates a turn-based workflow across multiple AI agents.

    Each agent:
    1. Gets a system prompt (base standards + role overlay + task context)
    2. Reads the session workspace to see what prior agents wrote
    3. Does its work using permitted tools
    4. Writes outputs to the session workspace
    5. Signals completion with write_agent_note

    The session blocks until all agents complete or an error stops the chain.
    """

    def __init__(
        self,
        project_root:  str,
        task:          str,
        agents:        list[AgentConfig],
        graph:         Optional[dict] = None,
        on_event:      Optional[Callable[[TeamEvent], None]] = None,
        session_id:    Optional[str] = None,
    ):
        self.project_root = os.path.abspath(project_root)
        self.task         = task
        self.agents       = agents
        self.graph        = graph
        self.on_event     = on_event or (lambda e: None)
        self.session_id   = session_id or uuid.uuid4().hex[:12]
        self.session_dir  = os.path.join(
            project_root, ".side", "session", self.session_id)
        os.makedirs(self.session_dir, exist_ok=True)

        self._result = WorkflowResult(
            session_id   = self.session_id,
            task         = task,
            project_root = self.project_root,
            session_dir  = self.session_dir,
        )
        self._stop_event = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> WorkflowResult:
        """Run the full workflow synchronously. Returns WorkflowResult."""
        self._write_task_brief()
        for cfg in self.agents:
            if self._stop_event.is_set():
                break
            turn = self._run_agent(cfg)
            self._result.turns.append(turn)
            if turn.error:
                self._result.error = f"{cfg.name} failed: {turn.error}"
                self._emit("error", cfg, turn.error)
                break
            self._emit("handoff", cfg,
                       f"{cfg.name} complete — "
                       f"{len(turn.session_files)} session file(s) written")
        if not self._result.error:
            self._result.verdict = "pending"   # human reviews to approve
        return self._result

    def run_async(self, callback: Callable[[WorkflowResult], None]) -> None:
        """Run the workflow in a background thread. Calls callback when done."""
        def _run():
            result = self.run()
            callback(result)
        threading.Thread(target=_run, daemon=True).start()

    def stop(self) -> None:
        """Request the workflow to stop after the current agent completes."""
        self._stop_event.set()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _write_task_brief(self) -> None:
        """Write the task description to the session workspace."""
        brief = (
            f"# Task\n\n{self.task}\n\n"
            f"# Session\n\nID: {self.session_id}\n"
            f"Project: {self.project_root}\n"
            f"Agents: {', '.join(f'{a.name} ({a.role})' for a in self.agents)}\n\n"
            f"# Instructions for all agents\n\n"
            f"1. Call list_session_files() first to see what prior agents wrote.\n"
            f"2. Read any relevant prior outputs before starting your work.\n"
            f"3. Write your outputs to the session workspace, not project source.\n"
            f"4. End your turn with write_agent_note summarising your work.\n"
        )
        brief_path = os.path.join(self.session_dir, "TASK.md")
        with open(brief_path, "w", encoding="utf-8") as f:
            f.write(brief)

    def _run_agent(self, cfg: AgentConfig) -> AgentTurn:
        """Run one agent's turn. Returns AgentTurn with full record."""
        t0 = time.time()
        self._emit("start", cfg, f"Starting {cfg.name} ({cfg.role})")

        # Build context
        permitted = cfg.permitted_tools or ROLE_TOOLS.get(cfg.role, frozenset())
        ctx = build_context(
            project_root = self.project_root,
            graph        = self.graph,
            role         = cfg.role,
            agent_name   = cfg.name,
            session_root = self.session_dir,
        )
        # Override with agent-specific permissions if set
        if cfg.permitted_tools:
            ctx = AppContext(
                **{**ctx.__dict__, "permitted_tools": cfg.permitted_tools}
            )

        # Build messages
        role_prompt   = get_role_prompt(cfg.role)
        session_ctx   = build_system_message(ctx)
        system_msg    = ChatMessage(
            role="system",
            content=role_prompt + "\n\n" + session_ctx.content
        )
        opening_msg   = ChatMessage(
            role="user",
            content=(
                f"Task: {self.task}\n\n"
                f"Session workspace: {self.session_dir}\n"
                f"Start by calling list_session_files() to see what prior agents wrote, "
                f"then proceed with your role."
            )
        )
        messages = [system_msg, opening_msg]

        # Filter TOOLS to this agent's permitted set
        agent_tools = [
            t for t in TOOLS
            if t["function"]["name"] in permitted
        ]

        # Run the agentic loop
        client = OllamaClient()
        tool_calls_log: list[dict] = []
        acc_text: list[str] = []

        def _on_text(chunk: str) -> None:
            acc_text.append(chunk)
            self._emit("text", cfg, chunk)

        def _dispatch(name: str, args: dict):
            self._emit("tool", cfg, f"{name}({json.dumps(args)[:80]})")
            result = dispatch_tool(name, args, ctx)
            snippet = result.content[:200] + ("…" if len(result.content) > 200 else "")
            self._emit("tool_result", cfg, snippet)
            tool_calls_log.append({
                "name": name, "args": args, "result": snippet
            })
            return result

        try:
            response = client.chat_with_tools(
                model       = cfg.model,
                messages    = messages,
                tools       = agent_tools,
                dispatch_fn = _dispatch,
                on_text     = _on_text,
                max_rounds  = cfg.max_rounds,
            )
            full_text = "".join(acc_text) or response.content
        except Exception as e:
            return AgentTurn(
                agent=cfg.name, role=cfg.role, model=cfg.model,
                response="", tool_calls=[], session_files=[],
                error=str(e), duration_s=time.time() - t0,
            )

        # Collect session files written this turn
        session_files = _list_session_files_written(self.session_dir)

        self._emit("done", cfg,
                   f"{cfg.name} finished in {time.time()-t0:.1f}s")

        return AgentTurn(
            agent        = cfg.name,
            role         = cfg.role,
            model        = cfg.model,
            response     = full_text,
            tool_calls   = tool_calls_log,
            session_files= session_files,
            duration_s   = time.time() - t0,
        )

    def _emit(self, event_type: str, cfg: AgentConfig, message: str,
              data: Optional[dict] = None) -> None:
        try:
            self.on_event(TeamEvent(
                type    = event_type,
                agent   = cfg.name,
                role    = cfg.role,
                message = message,
                data    = data or {},
            ))
        except Exception:
            pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _list_session_files_written(session_dir: str) -> list[str]:
    """List relative paths of all files in the session workspace."""
    if not os.path.isdir(session_dir):
        return []
    files = []
    for root, _, fnames in os.walk(session_dir):
        for f in fnames:
            full = os.path.join(root, f)
            files.append(os.path.relpath(full, session_dir))
    return sorted(files)


def load_session(session_dir: str) -> Optional[WorkflowResult]:
    """
    Reconstruct a WorkflowResult from a session directory.
    Returns None if the directory is missing or corrupt.
    """
    meta_path = os.path.join(session_dir, ".meta.json")
    if not os.path.isfile(meta_path):
        return None
    try:
        meta = json.load(open(meta_path, encoding="utf-8"))
        return WorkflowResult(
            session_id   = meta["session_id"],
            task         = meta["task"],
            project_root = meta["project_root"],
            session_dir  = session_dir,
            verdict      = meta.get("verdict", "pending"),
        )
    except Exception:
        return None


def list_sessions(project_root: str) -> list[dict]:
    """
    List all sessions for a project, newest first.
    Returns a list of dicts with id, task, verdict, session_dir.
    """
    sessions_dir = os.path.join(project_root, ".side", "session")
    if not os.path.isdir(sessions_dir):
        return []
    result = []
    for name in os.listdir(sessions_dir):
        sd = os.path.join(sessions_dir, name)
        task_path = os.path.join(sd, "TASK.md")
        if not os.path.isfile(task_path):
            continue
        try:
            task_line = open(task_path).readlines()[2].strip()  # line after "# Task\n\n"
        except Exception:
            task_line = ""
        result.append({
            "id":          name,
            "task":        task_line[:80],
            "session_dir": sd,
            "modified":    os.path.getmtime(sd),
        })
    result.sort(key=lambda x: -x["modified"])
    return result

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
