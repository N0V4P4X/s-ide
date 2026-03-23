# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
ai/manager.py
=============
The Manager — the user-facing agent in S-IDE.

The Manager is the orchestrator the user talks to directly. Its job is to:
  1. Understand the user's request
  2. Survey the project
  3. Write a structured plan
  4. Decide whether to handle simple tasks itself or delegate to a Team
  5. If delegating: select roles, define the workflow, trigger TeamSession
  6. Report results back to the user

The Manager is NOT a role in the Teams system — it sits above it.
It uses the full tool set (including write_file) and is always in "chat" mode.

Usage (from GUI)
----------------
    from ai.manager import Manager

    mgr = Manager(
        project_root=app.graph['meta']['root'],
        graph=app.graph,
        model="llama3.2",
        on_text=lambda chunk: app._ai_append(chunk),
        on_team_event=lambda evt: app._log_team_event(evt),
        on_graph_changed=lambda: app._load_project(root),
    )

    # User sends a message
    mgr.send("Build a calculator with GUI and CLI that handles PEMDAS")

    # Start a time-limited bake
    mgr.bake(task="Optimise the parser", minutes=10)

Manager system prompt
---------------------
Extends the base standards with:
- How to decide between direct action vs. team delegation
- How to write a plan that the team can execute
- How to use create_plan / create_team_workflow
- The "bake" concept: time-boxed autonomous development
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ai.client import OllamaClient, ChatMessage
from ai.context import AppContext, build_context, build_system_message
from ai.tools import TOOLS, dispatch_tool
from ai.tool_builder import (
    ToolMissingError, ToolBuildSpec, infer_tool_spec,
    register_custom_tool, load_all_custom_tools,
    get_custom_schemas, dispatch_custom, is_custom_tool,
)
from ai.models import get_model_for_role
from ai.standards import get_system_prompt


# ── Manager system prompt ─────────────────────────────────────────────────────

MANAGER_PROMPT = get_system_prompt("chat") + """

## You are the Manager

You are the user-facing orchestrator in S-IDE. You have real tools. Use them.

Your FIRST action on any message is ALWAYS to survey the loaded project:
  call get_graph_overview → understand the structure
  call read_file on key files → understand the code

NEVER describe a plan to use tools. Execute the tools immediately.

### When to act directly (do it yourself)
- Answering a question about the codebase
- A single small bug fix
- Updating a README or docstring

### When to delegate to a team (REQUIRED for everything else)
You MUST delegate when the task involves:
- Building any new feature
- Baking (time-limited autonomous development) — ALWAYS delegate
- Refactoring or optimising
- Writing tests for a module
- Any task that takes more than one function to complete

The user asked you to "bake"? You delegate. No exceptions.
The user asked you to "start a team"? You delegate. No exceptions.
The user described a feature? You delegate. No exceptions.

### Mandatory first steps for ANY non-trivial task
1. Call `get_graph_overview` — understand the project
2. Call `read_file` on the relevant entry points
3. Call `create_plan` — write the plan to .side/plan/task.md
4. Emit the `run_team` JSON block — the GUI will start the workflow

### How to delegate to a team
When you decide to delegate (which is most of the time):
1. Call get_graph_overview and read key files first
2. Call create_plan with your analysis
3. Return EXACTLY this JSON block — the GUI reads it to start the workflow:

```json
{
  "action": "run_team",
  "task": "The exact task description for the team",
  "agents": [
    {"role": "architect", "model": "llama3.2"},
    {"role": "implementer", "model": "codellama"},
    {"role": "reviewer", "model": "llama3.2"},
    {"role": "tester", "model": "llama3.2"}
  ],
  "session_note": "Brief note on what you've already decided/written"
}
```

The GUI will extract this JSON and offer to run it as a team workflow.

### New project standard
When asked to create a new project, create this structure:
```
<project-name>/
├── side.project.json
├── README.md
├── src/
│   ├── __init__.py
│   └── main.py
└── test/
    ├── __init__.py
    └── test_main.py
```

side.project.json must include:
- name, version (0.1.0), description
- run scripts: "run", "test"
- ignore: ["__pycache__", "*.pyc", ".side", "dist"]

README.md must include:
- Project purpose (one paragraph)
- Quick start (how to run and test)
- Architecture (what each file/module does)

### Git workflow
Use git tools to maintain a clean history. Standard workflow for any change:
1. `git status` — see what's changed
2. `git diff` — review changes before staging
3. `git add` / `git add_all` — stage what's ready
4. `git diff_staged` — verify what's staged
5. `git commit` with a descriptive message (imperative mood, <50 chars)
6. `git log` — confirm the commit landed

Remote: `git pull` to sync from remote, `git push` to upload.
Branches: `git checkout_new` to create, `git push` when done.
When asked to commit: use commit_all with a meaningful message, not "automated commit".

### Profiling
When asked about performance, slow code, or bottlenecks:
1. Call `profile_project` — runs cProfile on the project entry point
2. Read the top functions from the result
3. Use `read_file` on the slow functions to understand why
4. Propose concrete fixes with before/after complexity estimates
Do NOT add @timed decorators. Use profile_project instead.

### The Bake
A bake is time-limited TEAM development. When the user says "bake":
1. Call get_graph_overview immediately — do NOT ask what to do first
2. Read the key source files to understand what exists
3. Call create_plan — what will the team build in this time budget?
4. Emit the run_team JSON block — the GUI starts the workflow
5. The TEAM does the work, not you

A bake that does not emit run_team has failed. Always delegate.
Always end a bake with a complete, passing test suite.

### Communication style
- Think with <thought> blocks — but CALL tools after, do not narrate them
- One sentence summary of your plan, then immediately start executing
- Never write 'I will now do X' without calling the tool that does X
- If delegating to a team, one sentence explaining the role split
- Keep the user informed every 2-3 tool calls

### When the user says 'Continue' or something short/ambiguous
1. Call get_graph_overview immediately — understand the current state
2. If a plan exists (.side/plan/task.md), call read_session_file or
   read_file to see what's there
3. Pick up where the work left off, or ask ONE question if genuinely unclear
Never respond to 'Continue' with an explanation of what you plan to do.
Just do it.

### Never use placeholder paths
Never call tools with placeholder values like '<relevant_file>',
'identified_path', 'path/to/file', or '<path>'. If you do not know
the path, call list_files or get_graph_overview first to find it.
"""


# ── Manager state ─────────────────────────────────────────────────────────────

@dataclass
class BakeResult:
    task:        str
    duration_s:  float
    completed:   list[str] = field(default_factory=list)
    remaining:   list[str] = field(default_factory=list)
    summary_path: str = ""
    team_result:  object = None   # WorkflowResult if team was used


class Manager:
    """
    The user-facing orchestrator agent.

    Maintains conversation history across turns. Can be interrupted.
    Supports both single-turn Q&A and time-limited autonomous bakes.
    """

    def __init__(
        self,
        project_root:     str = "",
        graph:            Optional[dict] = None,
        model:            str = "llama3.2",
        on_text:          Optional[Callable[[str], None]] = None,
        on_tool:          Optional[Callable[[str, dict], None]] = None,
        on_team_event:    Optional[Callable[[object], None]] = None,
        on_graph_changed: Optional[Callable[[], None]] = None,
        on_done:          Optional[Callable[[], None]] = None,
        on_log:           Optional[Callable[[str, str], None]] = None,
        on_tool_missing:  Optional[Callable[['ToolBuildSpec'], None]] = None,
    ):
        self.project_root     = os.path.abspath(project_root) if project_root else ""
        self.graph            = graph
        self.model            = model
        self.on_text          = on_text or (lambda c: None)
        self.on_tool          = on_tool or (lambda n, a: None)
        self.on_team_event    = on_team_event or (lambda e: None)
        self.on_graph_changed = on_graph_changed or (lambda: None)
        self.on_done          = on_done or (lambda: None)
        self.on_log           = on_log or (lambda t, tag='dim': None)
        self.on_tool_missing  = on_tool_missing or (lambda spec: None)
        self._pending_tool_spec: ToolBuildSpec | None = None
        self._tool_missing_event = threading.Event()
        self._do_turn_depth: int = 0  # guard against nested _do_turn

        self._client    = OllamaClient()
        self._messages: list[ChatMessage] = []
        self._running   = threading.Event()
        self._stop      = threading.Event()
        self._bake_deadline: Optional[float] = None

        self._ctx = self._build_ctx()
        self._reset_system_message()
        
        # Governance state
        self._audit_log: list[dict] = []
        self._team_action_emitted = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def send(self, prompt: str) -> None:
        """Send a message from the user. Runs in a background thread."""
        self._messages.append(ChatMessage(role="user", content=prompt))
        self._stop.clear()
        threading.Thread(target=self._run_turn, daemon=True).start()

    def bake(self, task: str, minutes: int = 10) -> None:
        """
        Start a time-limited autonomous development session.
        Runs until the time limit or stop() is called.
        """
        self._bake_deadline = time.time() + minutes * 60
        self._stop.clear()
        prompt = (
            f"BAKE SESSION — {minutes} minute time limit.\n\n"
            f"Task: {task}\n\n"
            f"Work autonomously. Announce your plan, execute it step by step, "
            f"stop cleanly before the deadline, and write a bake summary."
        )
        self.send(prompt)

    def stop(self) -> None:
        """Request the current run to stop after the next tool call completes."""
        self._stop.set()

    def update_project(self, project_root: str, graph: dict) -> None:
        """Update the manager's view of the current project (called after re-parse)."""
        self.project_root = os.path.abspath(project_root)
        self.graph = graph
        self._ctx = self._build_ctx()
        self._reset_system_message()

    def clear_history(self) -> None:
        """Clear conversation history but keep system message."""
        self._messages = []
        self._reset_system_message()

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    # ── Internal ───────────────────────────────────────────────────────────────

    def approve_tool_build(
        self,
        built_tool_path: str | None,
    ) -> None:
        """
        Called by the GUI after the tool-building team completes.

        Args:
            built_tool_path: Path to the built .py file, or None if rejected/failed.
        """
        if built_tool_path and os.path.isfile(built_tool_path):
            try:
                register_custom_tool(built_tool_path)
            except Exception as e:
                self.on_text(f"\n[tool registration failed: {e}]\n")
        self._pending_tool_spec = None
        self._tool_missing_event.set()  # unblock _do_turn

    def reject_tool_build(self) -> None:
        """Called by the GUI when the user declines to build the tool."""
        self._pending_tool_spec = None
        self._tool_missing_event.set()  # unblock _do_turn

    def _build_ctx(self) -> AppContext:
        return build_context(
            project_root=self.project_root,
            graph=self.graph,
            role="chat",
        )

    def _reset_system_message(self) -> None:
        ctx_msg = build_system_message(self._ctx)
        system_content = MANAGER_PROMPT
        if self.project_root:
            system_content += "\n\n" + ctx_msg.content
        if not self._messages or self._messages[0].role != "system":
            self._messages.insert(0, ChatMessage(role="system",
                                                  content=system_content))
        else:
            self._messages[0] = ChatMessage(role="system",
                                             content=system_content)

    def _run_turn(self) -> None:
        self._running.set()
        self._do_turn_depth = 0
        try:
            self._do_turn()
        finally:
            self._do_turn_depth = 0
            self._running.clear()
            self.on_done()

    def _do_turn(self) -> None:
        self._do_turn_depth += 1
        if self._do_turn_depth > 3:
            self.on_text("\n[Manager: max recursion depth — stopping]\n")
            self._do_turn_depth -= 1
            return
        acc: list[str] = []

        def _on_text(chunk: str) -> None:
            acc.append(chunk)
            self.on_text(chunk)
            self.on_log(chunk, "dim")
            # Check for team delegation JSON in streamed text
            self._check_for_team_action("".join(acc))

        def _dispatch(name: str, args: dict):
            self.on_tool(name, args)
            # 1. PRE-AUDIT: Check for suspicious claims
            self._shadow_audit_pre(name, args)

            # Execution
            if name == "write_file":
                self.after_write()
            
            bd = self._bake_deadline
            if bd is not None and time.time() > bd:
                self._stop.set()
                
            if is_custom_tool(name):
                cr = dispatch_custom(name, args, self._ctx)
                if cr is not None:
                    from ai.client import ToolResult
                    import json as _json
                    res = ToolResult(tool_call_id='', name=name, content=_json.dumps(cr))
                    self._shadow_audit_post(name, args, res)
                    return res
            
            result = dispatch_tool(name, args, self._ctx)
            
            # 2. POST-AUDIT: Check result validity
            self._shadow_audit_post(name, args, result)
            
            # Detect unknown tool — offer to build it
            try:
                import json as _json
                payload = _json.loads(result.content)
                if isinstance(payload, dict) and payload.get('error', '').startswith('Unknown tool'):
                    snippet = "".join(acc)
                    intent = snippet[max(0, len(snippet)-400):] if snippet else ''
                    spec = infer_tool_spec(
                        ToolMissingError(tool_name=name, tool_args=args, intent=intent),
                        self.project_root,
                    )
                    raise ToolMissingError(tool_name=name, tool_args=args, intent=intent)
            except (_json.JSONDecodeError, AttributeError):
                pass
            return result

        try:
            # Include custom tools in the schema
            live_tools = list(TOOLS) + get_custom_schemas()
            # Also load any tools written during this session
            if self.project_root:
                load_all_custom_tools(self.project_root)
                live_tools = list(TOOLS) + get_custom_schemas()
            # Perform context pruning / summarization if history is long
            if len(self._messages) > 24:
                self._summarize_history()
            
            # Trim strictly for model safety
            MAX_MSGS = 32
            if len(self._messages) > MAX_MSGS:
                system = [m for m in self._messages if m.role == 'system']
                rest   = [m for m in self._messages if m.role != 'system']
                self._messages = system + rest[-(MAX_MSGS - len(system)):]
            response = self._client.chat_with_tools(
                model=self.model,
                messages=self._messages,
                tools=live_tools,
                dispatch_fn=_dispatch,
                on_text=_on_text,
                max_rounds=12,
                stop_event=self._stop,
            )
            full = "".join(acc) or response.content
            self._messages.append(ChatMessage(role="assistant", content=full))
        except ToolMissingError as missing:
            # Pause, ask user to approve building the tool
            spec = infer_tool_spec(missing, self.project_root)
            self._pending_tool_spec = spec
            self.on_tool_missing(spec)
            # Block this thread until the GUI resolves (approve/reject)
            self._tool_missing_event.wait(timeout=300)  # 5 min max
            self._tool_missing_event.clear()
            # If approved, the tool was built & registered; resume
            if is_custom_tool(missing.tool_name):
                self.on_text(
                    f"\n✓ Tool '{missing.tool_name}' built. Resuming...\n")
                self.on_log(
                    f"Tool '{missing.tool_name}' registered. Resuming turn.\n",
                    'tool')
                # Re-run the turn with the new tool available
                self._do_turn()
            else:
                self.on_text(
                    f"\n✗ Tool '{missing.tool_name}' not built or rejected. "
                    f"Continuing without it.\n")
        except Exception as e:
            self.on_text(f"\n[Manager error: {e}]\n")
        finally:
            self._do_turn_depth = max(0, self._do_turn_depth - 1)

    def after_write(self) -> None:
        """Called after any write_file tool — triggers graph re-parse."""
        if self.project_root:
            threading.Thread(
                target=self._refresh_graph, daemon=True).start()

    def _refresh_graph(self) -> None:
        try:
            from parser.project_parser import parse_project
            g = parse_project(self.project_root, save_json=True)
            self.graph = g.to_dict()
            self._ctx = self._build_ctx()
            self.on_graph_changed()
        except Exception:
            pass

    def _check_for_team_action(self, text: str) -> None:
        if self._team_action_emitted:
            return
        import re
        m = re.search(r'```json\s*(\{[^`]+?"action"\s*:\s*"run_team"[^`]+?\})\s*```',
                      text, re.DOTALL)
        if m:
            try:
                action = json.loads(m.group(1))
                if action.get("action") == "run_team":
                    self._team_action_emitted = True
                    # Fill missing models with role specialist defaults
                    for agent in action.get("agents", []):
                        if not agent.get("model"):
                            agent["model"] = get_model_for_role(agent["role"])
                    self.on_team_event(action)
            except json.JSONDecodeError:
                pass

    def _shadow_audit_pre(self, name: str, args: dict):
        """Invisible check before tool execution."""
        # E.g. Check for path safety, or if AI is hallucinating a file
        pass

    def _shadow_audit_post(self, name: str, args: dict, result: object):
        """Invisible verification after tool execution."""
        msg = f"Audit: {name} called."
        if name == "write_file":
            path = args.get("path")
            if path and not os.path.isabs(path):
                path = os.path.join(self.project_root, path)
            if not os.path.exists(path or ""):
                 self.on_log(f"ALERT: AI claimed to write {args.get('path')} but file missing!", "warn")
        
        self._audit_log.append({"tool": name, "args": args, "time": time.time()})

    def _summarize_history(self):
        """Condense old conversation to save context."""
        msg_list = self._messages
        if len(msg_list) < 10: return
        
        # Keep system, Keep very recent (last 4), Condense middle
        system = [m for m in msg_list if m.role == 'system']
        others = [m for m in msg_list if m.role != 'system']
        
        if len(others) < 10: return
        
        split_idx = max(0, len(others) - 4)
        recent = others[split_idx:]
        middle = others[:split_idx]
        
        # Keep only the last 2 of the middle for continuity
        mid_idx = max(0, len(middle) - 2)
        comp_mid = middle[mid_idx:]
        self._messages = system + comp_mid + recent
        self.on_log(f"Context management: pruned history to {len(self._messages)} messages.", "dim")


# ── Project scaffold ──────────────────────────────────────────────────────────

def scaffold_new_project(parent_dir: str, name: str,
                          description: str = "") -> str:
    """
    Create a minimal S-IDE project in parent_dir/<name>/.
    Returns the absolute path to the new project root.

    Structure created:
        <name>/
        ├── side.project.json
        ├── README.md
        ├── src/
        │   ├── __init__.py
        │   └── main.py
        └── test/
            ├── __init__.py
            └── test_main.py
    """
    slug = name.lower().replace(" ", "-").replace("_", "-")
    root = os.path.join(os.path.abspath(parent_dir), slug)
    os.makedirs(root, exist_ok=True)

    # side.project.json
    cfg = {
        "name": slug,
        "version": "0.1.0",
        "description": description or f"{name} — created by S-IDE",
        "ignore": ["__pycache__", "*.pyc", ".side", "dist", "*.egg-info"],
        "run": {
            "run":  f"python src/main.py",
            "test": "python -m pytest test/ -v" if _has_pytest() else
                    "python -m unittest discover test/",
        },
        "versions": {"dir": "versions", "compress": True, "keep": 10},
    }
    with open(os.path.join(root, "side.project.json"), "w") as f:
        json.dump(cfg, f, indent=2)

    # README.md
    readme = (f"# {name}\n\n"
              f"{description or 'Add a description here.'}\n\n"
              f"## Quick start\n\n"
              f"```bash\npython src/main.py   # run\n"
              f"python -m unittest discover test/   # test\n```\n\n"
              f"## Architecture\n\n"
              f"- `src/main.py` — entry point\n"
              f"- `test/test_main.py` — unit tests\n")
    with open(os.path.join(root, "README.md"), "w") as f:
        f.write(readme)

    # src/
    src_dir = os.path.join(root, "src")
    os.makedirs(src_dir, exist_ok=True)
    with open(os.path.join(src_dir, "__init__.py"), "w") as f:
        f.write("")
    with open(os.path.join(src_dir, "main.py"), "w") as f:
        f.write(
            f'"""\n{name}\n{"=" * len(name)}\n{description or "Entry point."}\n"""\n\n\n'
            f'def main():\n    print("Hello from {name}!")\n\n\n'
            f'if __name__ == "__main__":\n    main()\n'
        )

    # test/
    test_dir = os.path.join(root, "test")
    os.makedirs(test_dir, exist_ok=True)
    with open(os.path.join(test_dir, "__init__.py"), "w") as f:
        f.write("")
    with open(os.path.join(test_dir, "test_main.py"), "w") as f:
        f.write(
            f'"""Tests for {name}."""\nimport unittest\nimport sys\nimport os\n\n'
            f'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))\n\n\n'
            f'class TestMain(unittest.TestCase):\n\n'
            f'    def test_placeholder(self):\n'
            f'        """Replace with real tests."""\n'
            f'        self.assertTrue(True)\n\n\n'
            f'if __name__ == "__main__":\n    unittest.main()\n'
        )

    return root


def _has_pytest() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("pytest") is not None
    except Exception:
        return False

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
