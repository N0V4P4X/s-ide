# S-IDE — v0.5.0

**Systematic Integrated Development Environment** — a project graph editor with an embedded AI development assistant, built entirely in Python.

S-IDE parses any Python project into a live dependency graph, lets you navigate and inspect it visually, gives a team of AI agents direct tool access to your codebase, profiles execution with cProfile, and monitors live performance on node cards.

---

## Architecture

```
s-ide-py/
├── gui/
│   ├── app.py               # Main window, canvas, topbar, inspector
│   ├── teams_canvas.py      # AI Teams workflow designer mixin
│   ├── panels.py            # Bottom panel tab builders
│   ├── markdown.py          # Markdown→Tk renderer
│   ├── editor.py            # Syntax-highlighted source editor
│   ├── state.py             # Session persistence (~/.s-ide-state.json)
│   ├── log.py               # Rotating log + in-memory ring
│   └── server.py            # Optional HTTP+SSE bridge
├── ai/
│   ├── client.py            # Ollama HTTP client, streaming, tool loop
│   ├── tools.py             # 18 tool definitions + permission-aware dispatch
│   ├── context.py           # AppContext, role permissions
│   ├── standards.py         # Base system prompt
│   ├── manager.py           # Manager orchestrator + scaffold_new_project
│   ├── teams.py             # TeamSession: turn-based multi-agent engine
│   ├── playground.py        # Isolated Python sandbox
│   ├── tool_builder.py      # Self-improving tool creation workflow
│   └── roles/               # Role definitions (6 roles)
├── monitor/
│   ├── profiler.py          # cProfile-based live project profiler ← use this
│   ├── perf.py              # ParseTimer, ProcessMonitor, MetricsWatcher
│   ├── instrument.py        # @timed decorator (legacy)
│   └── instrumenter.py      # Bulk instrumentation (legacy)
├── parser/                  # Project analysis pipeline
│   ├── project_parser.py    # Orchestrator: walk→parse→edges→layout→audit
│   └── parsers/             # python, js, json, shell, toml/yaml
├── graph/types.py           # FileNode, Edge, ProjectGraph, Definition
├── build/                   # clean, minify, package, sandbox
├── process/                 # Subprocess lifecycle management
├── version/                 # Snapshot, restore, apply-update
├── examples/calculator/     # Reference project: PEMDAS GUI + CLI calc
├── test/test_suite.py       # 280 tests, 43 classes, stdlib unittest
├── CHANGELOG.md
├── FUTURE.md
└── update.py                # Self-update (version-sorted tarball selection)
```

---

## Quick start

```bash
python gui/app.py                        # launch GUI
python main.py parse /path/to/project   # parse from CLI
python test/test_suite.py               # run tests
python update.py                        # self-update from ~/Downloads/
```

---

## GUI layout

```
┌──────────────────────────────────────────────────────────────┐
│ TOPBAR  logo · project · [PY JS CFG DOCS] · ⚡TEAMS · ⏱Profile│
├───────────────────────────────────────────────┬──────────────┤
│  CANVAS                                       │  INSPECTOR   │
│  • node cards (one per source file)           │  (on click)  │
│  • bezier import edges                        │              │
│  • dashed doc→source links                   │              │
│  • live @timed / cProfile overlays            │              │
├───────────────────────────────────────────────┴──────────────┤
│  ▓ resize handle                                              │
├───────────────────────────────────────────────────────────────┤
│  Projects │ AI Chat │ Plan │ Playground │ Terminal │ Teams Log │
└───────────────────────────────────────────────────────────────┘
```

**⚡ TEAMS** — switch canvas to AI Teams workflow designer  
**⏱ Profile** — run cProfile on project entry point, update node overlays

---

## AI assistant (18 tools)

Requires [Ollama](https://ollama.ai) running locally.

```bash
ollama serve && ollama pull llama3.2
```

| Category | Tools |
|---|---|
| Read | `read_file`, `list_files`, `get_file_summary`, `search_definitions`, `get_graph_overview`, `get_definition_source` |
| Run | `run_command`, `run_in_playground`, `get_metrics` |
| Write | `write_file`, `create_plan`, `update_plan`, `write_agent_note` |
| Session | `write_session_file`, `read_session_file`, `list_session_files` |
| Git | `git` (22 commands: status, log, diff, add, commit, push, pull, branch, stash, blame, …) |
| Profiling | `profile_project` |

The Manager bot surveys the project, writes a plan, and delegates complex tasks to a specialist team via `run_team` JSON. Bake sessions run autonomously for a time-boxed period. If the Manager calls a tool that doesn't exist, a team is offered to build it.

---

## Live profiling

```bash
# Via GUI: click ⏱ Profile in topbar
# Via Manager: "Profile the project"
# Via code:
from monitor.profiler import profile_project
result = profile_project("/path/to/project")
print(result.summary())
```

Results written to `.side-metrics.json`. Node cards update with colour-coded timing strips within ~1.5 seconds.

---

## AI Teams

Click **⚡ TEAMS** to open the workflow designer. Build agent sequences by adding role cards and connecting them. Click **▶ Run Workflow** in the Plan tab to start.

Teams Log tab shows full-verbosity event stream from all agents with timestamps. Session browser on the left lists past sessions — click to review plans, findings, and verdicts.

---

## Git integration

The `git` tool supports 22 commands. Standard workflow:
```
git status → git diff → git add_all → git diff_staged → git commit (message=…)
```

Push/pull with optional `remote` and `branch` params. `checkout_new` for feature branches. `blame`, `reset`, `tag` for deeper operations.

---

## Self-update

```bash
python update.py   # picks highest-versioned s-ide-*.tar.gz from ~/Downloads/
```

---

## Versioning

Semantic versioning. See `CHANGELOG.md` for history, `FUTURE.md` for roadmap.
