# S-IDE — v0.4.0

**Systematic Integrated Development Environment** — a project graph editor with an embedded AI development assistant, built entirely in Python.

S-IDE parses your project into a live dependency graph, lets you navigate and inspect it visually, and gives a team of AI agents direct access to your codebase so they can read, analyse, plan, and execute development work.

---

## Architecture

```
s-ide-py/
├── gui/
│   ├── app.py               # Core shell, window mapping, topbar
│   ├── ai_mixin.py          # AI assistant, manager integration
│   ├── canvas_mixin.py      # Rendering, viewport, input, grid
│   ├── dialogs_mixin.py     # Process, log, and build panels
│   ├── inspector_mixin.py   # Slide-in node detail panel
│   ├── panels.py            # Bottom panel tab builders
│   ├── markdown.py          # Markdown→Tk renderer (no display at import)
│   ├── editor.py            # Syntax-highlighted source editor
│   ├── state.py             # Session persistence (~/.s-ide-state.json)
│   ├── log.py               # Rotating log + in-memory ring
│   └── server.py            # Optional HTTP+SSE bridge
├── ai/
│   ├── client.py            # Ollama HTTP client, streaming, tool loop
│   ├── tools.py             # 14 tool definitions + dispatch
│   ├── context.py           # AppContext built from live graph
│   └── standards.py         # System prompt: dev standards + tool rules
├── parser/
│   ├── project_parser.py    # Orchestrator: walk→parse→edges→layout→audit
│   ├── walker.py            # Directory traversal + ignore patterns
│   ├── project_config.py    # side.project.json read/write/bump
│   ├── resolve_edges.py     # Import strings → graph edges
│   ├── layout.py            # Topological x/y assignment
│   ├── doc_check.py         # README staleness audit
│   └── parsers/             # python, js, json, shell, toml/yaml
├── graph/
│   └── types.py             # FileNode, Edge, ProjectGraph, Definition
├── monitor/
│   ├── perf.py              # ParseTimer, ProcessMonitor, MetricsWatcher
│   ├── instrument.py        # @timed → .side-metrics.json
│   └── instrumenter.py      # Bulk-instrument a project
├── build/
│   ├── cleaner.py           # Tiered artifact removal
│   ├── minifier.py          # Strip comments/docstrings, bundle modules
│   ├── packager.py          # tarball / installer / portable
│   └── sandbox.py           # Run in isolated temp copy
├── process/
│   └── process_manager.py   # Spawn/stop/suspend subprocesses
├── version/
│   └── version_manager.py   # Snapshot, restore, apply-update
├── test/
│   └── test_suite.py        # 184 tests, 33 classes, stdlib unittest
├── CHANGELOG.md
├── FUTURE.md                # Roadmap and long-term vision
├── main.py                  # CLI
└── update.py                # Self-update
```

---

## Quick start

```bash
# Launch GUI
python gui/app.py

# Parse a project (CLI)
python main.py parse /path/to/project

# Health check loop (tests + parse + doc audit)
python main.py self-check .

# Build a distributable tarball
python main.py build . --kind tarball --bump patch

# Self-update from ~/Downloads/
python update.py
```

## Self-improvement

S-IDE can validate and analyze itself via `self-check`. See [`SELF_IMPROVEMENT.md`](SELF_IMPROVEMENT.md) for the full loop (CI, strict docs mode, and safer self-updates).

## GUI panels

## GUI layout

```
┌──────────────────────────────────────────────────────┐
│ TOPBAR  logo · project · filter chips · search · zoom │
├───────────────────────────────────────────┬───────────┤
│  CANVAS                                   │ INSPECTOR │
│  • node cards (one per file)              │ (on click)│
│  • bezier import edges                    │           │
│  • dashed doc→source links                │           │
│  • live @timed overlays                   │           │
├───────────────────────────────────────────┴───────────┤
│  ▓ resize handle                                      │
├───────────────────────────────────────────────────────┤
│  BOTTOM PANEL  Projects│AI Chat│Plan│Playground│Terminal│
└───────────────────────────────────────────────────────┘
```

Double-click a node → editor. Right-click → context menu. Filter chips are multi-select; docs and config hidden by default.

---

## AI assistant

Requires [Ollama](https://ollama.ai) running locally.

```bash
ollama serve && ollama pull llama3.2
```

14 tools: `read_file`, `list_files`, `get_file_summary`, `search_definitions`, `get_graph_overview`, `get_metrics`, `run_command`, `get_definition_source`, `write_file`, `create_plan`, `update_plan`, `write_agent_note`, `run_in_playground`, `git`.

The system prompt enforces tool-first behaviour: the AI reads before answering, never guesses, and follows the project's development standards.

---

## side.project.json

Auto-created on first parse. Controls name, version, ignore patterns, run scripts, and version archive settings.

---

## Self-update

Drop `s-ide-v<version>.tar.gz` in `~/Downloads/` and run `python update.py`. Picks the highest-versioned tarball, archives current state first, then relaunches.

---

## Versioning

Semantic versioning. See `CHANGELOG.md` for history, `FUTURE.md` for roadmap.
