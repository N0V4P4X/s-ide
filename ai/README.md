# ai/

Ollama-powered AI assistant, multi-agent team system, and self-improving tool
creation for S-IDE.

## Modules

```
ai/
├── client.py        — Ollama HTTP client (streaming, tool-calling loop)
├── tools.py         — 17 built-in tool definitions + permission-aware dispatch
├── context.py       — AppContext, role permissions, build_system_message
├── standards.py     — Base system prompt: dev standards + tool-use rules
├── manager.py       — Manager: user-facing orchestrator + scaffold_new_project
├── teams.py         — TeamSession: turn-based multi-agent workflow engine
├── playground.py    — Isolated sandbox for agent code execution
├── tool_builder.py  — Self-improving tool creation workflow
└── roles/
    ├── __init__.py
    └── definitions.py — Role definitions: prompts, tool sets, output paths
```

---

## manager.py — The Manager

The user-facing orchestrator. Sits above the specialist team. Understands the
user's request, surveys the project, writes a plan, then either acts directly
(simple tasks) or delegates to a team (everything else).

```python
from ai.manager import Manager, scaffold_new_project

mgr = Manager(
    project_root     = "/path/to/project",
    graph            = graph_dict,
    model            = "llama3.2",
    on_text          = lambda chunk: print(chunk, end=""),
    on_tool          = lambda name, args: print(f"→ {name}"),
    on_team_event    = lambda evt: print(f"[{evt.agent}] {evt.message}"),
    on_graph_changed = lambda: reload_project(),
    on_done          = lambda: update_ui(),
    on_log           = lambda text, tag: log_to_teams_panel(text, tag),
    on_tool_missing  = lambda spec: show_build_dialog(spec),
)

mgr.send("Check out the calculator project and tell me what to do next")
mgr.bake(task="Add history tracking to the calculator GUI", minutes=30)
mgr.stop()   # clean stop after current tool call
```

**Delegation protocol:** when the Manager decides a task is complex, it emits
a `run_team` JSON block in its response. The GUI detects this, populates the
Teams canvas, and starts the workflow automatically.

```json
{
  "action": "run_team",
  "task": "Add history panel to calculator GUI",
  "agents": [
    {"role": "architect",   "model": "llama3.2"},
    {"role": "implementer", "model": "codellama"},
    {"role": "reviewer",    "model": "llama3.2"},
    {"role": "tester",      "model": "llama3.2"}
  ]
}
```

**New project scaffolding:**

```python
root = scaffold_new_project(
    parent_dir  = "~/DevOps",
    name        = "my-app",
    description = "A new S-IDE project",
)
```

Creates `side.project.json`, `README.md`, `src/main.py`, `test/test_main.py`.

---

## tool_builder.py — Self-improving tool creation

When the Manager calls a tool that doesn't exist, the workflow pauses and
offers to build it:

```
Manager calls unknown_tool(args)
    → dispatch detects "Unknown tool: X"
    → ToolMissingError raised (tool_name, tool_args, intent)
    → on_tool_missing(spec) called → GUI shows approval dialog
    → User approves → build_tool_with_team() runs 3-agent team
    → Tool written to .side/tools/<name>.py
    → register_custom_tool() loads it into the registry
    → Manager resumes with the new tool available
```

**Custom tool format** (what the team writes):

```python
# .side/tools/my_tool.py

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "What it does",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "The input"}
            },
            "required": ["input"]
        }
    }
}

def TOOL_HANDLER(args: dict, ctx) -> dict:
    return {"result": args["input"].upper()}
```

**Registry API:**

```python
from ai.tool_builder import (
    register_custom_tool,    # load one tool file
    load_all_custom_tools,   # load all from .side/tools/
    get_custom_schemas,      # include in TOOLS list for Ollama
    dispatch_custom,         # call a custom tool
    is_custom_tool,          # check registry
)

# Load all custom tools for a project at startup
load_all_custom_tools("/path/to/project")
```

Custom tools persist across sessions — `load_all_custom_tools()` is called
at the start of every Manager turn.

---

## client.py

```python
from ai.client import OllamaClient

client = OllamaClient()
client.is_available()      # True if ollama serve is running
client.list_models()       # ["llama3.2", "codellama", ...]

response = client.chat_with_tools(
    model="llama3.2", messages=messages, tools=TOOLS,
    dispatch_fn=dispatch_tool, on_text=lambda c: print(c, end=""),
    max_rounds=12, stop_event=threading.Event(),
)
```

---

## tools.py — 17 built-in tools

| Tool | Description | Blocked for |
|---|---|---|
| `read_file` | Read project file (≤8000 chars) | — |
| `list_files` | List files, filter by ext/subdir | — |
| `get_file_summary` | Imports, exports, defs with complexity | — |
| `search_definitions` | Find functions/classes by name | — |
| `get_graph_overview` | Structure, stats, doc health | — |
| `get_metrics` | Live timing from `.side-metrics.json` | — |
| `get_definition_source` | Source lines for a function/class | — |
| `git` | status, log, diff, branch, commit, … | — |
| `run_command` | Run a `side.project.json` script | Reviewer, Documentarian |
| `run_in_playground` | Execute Python in isolated sandbox | Reviewer, Architect, Doc |
| `write_file` | Write/overwrite a project source file | Reviewer, Tester, Doc |
| `write_session_file` | Write to session workspace | — |
| `read_session_file` | Read prior agent's session output | — |
| `list_session_files` | List session workspace files | — |
| `create_plan` | Create structured plan in `.side/plan/` | — |
| `update_plan` | Update an in-progress plan | — |
| `write_agent_note` | Leave a handoff note for the next agent | — |

---

## context.py — AppContext and permissions

```python
from ai.context import build_context, ROLE_TOOLS

ctx = build_context(
    project_root="/path", graph=graph_dict,
    role="reviewer", agent_name="Alice",
    session_root="/path/.side/session/abc123",
)

ctx.can_use("write_file")        # False — reviewers cannot
ctx.can_use("write_session_file") # True — all roles can
```

Role permission matrix: see `ROLE_TOOLS` in `context.py`.

---

## teams.py — TeamSession

```python
from ai.teams import TeamSession, AgentConfig

session = TeamSession(
    project_root = "/path/to/project",
    task         = "Add input validation to parse_file",
    agents       = [
        AgentConfig(role="architect",   model="llama3.2"),
        AgentConfig(role="implementer", model="codellama"),
        AgentConfig(role="reviewer",    model="llama3.2"),
        AgentConfig(role="tester",      model="llama3.2"),
    ],
    on_event = lambda e: print(f"[{e.agent}] {e.message}"),
)
result = session.run()
result.apply()   # promote session outputs to real project
```

Session workspace: `.side/session/<id>/` — all agents write here.
`result.apply()` promotes `implementation/` and `docs/` to the project.

---

## playground.py

Isolated Python execution dispatched by `run_in_playground` tool. Hard-linked
project snapshot, 10s timeout, no state between calls.

---

## roles/

Six role definitions with prompts, permitted tools, output paths.
`get_role_prompt("reviewer")` returns base standards + role overlay.

---

## Running Ollama

```bash
ollama serve
ollama pull llama3.2
ollama pull codellama   # stronger for implementation
```

---

## standards.py

See `AGENT_STANDARDS.md` for the full dev standards and tool-use rules injected
into every conversation.
