# S-IDE — Python Core v0.2.0

Systematic Integrated Development Environment — Python backend and Tkinter GUI.

## Architecture

```
s-ide-py/
├── gui/
│   ├── app.py           # Tkinter desktop GUI (node graph editor)
│   ├── log.py           # Logging → logs/s-ide.log + in-memory ring buffer
│   └── server.py        # Optional HTTP+SSE bridge (headless/remote use)
│
├── parser/              # Project analysis engine
│   ├── project_parser.py   # Orchestrator → walks, parses, resolves, layouts, audits
│   ├── walker.py            # Directory traversal + ignore patterns
│   ├── project_config.py    # side.project.json read/write/init/bump
│   ├── resolve_edges.py     # Import strings → graph edges
│   ├── layout.py            # Topological auto-layout for node positions
│   ├── doc_check.py         # README staleness + empty-module audit
│   └── parsers/
│       ├── python_parser.py # AST-based (accurate, syntax-error fallback)
│       ├── js_parser.py     # ES/CJS/TS regex
│       ├── json_parser.py   # package.json, tsconfig, generic config
│       └── shell_parser.py  # source/export/function relationships
│
├── graph/
│   └── types.py         # FileNode, Edge, ProjectGraph, GraphMeta dataclasses
│
├── monitor/
│   └── perf.py          # ParseTimer (stage timings) + ProcessMonitor (CPU/RSS)
│
├── process/
│   └── process_manager.py  # Spawn/monitor/stop/suspend/resume subprocesses
│
├── version/
│   └── version_manager.py  # Snapshot, apply-update, list, compress via tarballs
│
├── build/
│   ├── cleaner.py       # Remove caches, logs, build artifacts by tier
│   ├── minifier.py      # Strip comments/docstrings; bundle modules
│   └── packager.py      # Produce tarball, installer, or portable package
│
├── test/
│   └── test_suite.py    # 86 unit tests (stdlib unittest, no pytest needed)
│
├── logs/                # s-ide.log lives here (created on first launch)
├── versions/            # Project snapshots (created on first archive)
│
├── main.py              # CLI: parse | run | archive | update | build | versions
└── update.py            # Self-update: finds newest s-ide*.tar.gz in ~/Downloads/
```

## Quick start

```bash
# Launch GUI
python gui/app.py

# Parse a project (CLI)
python main.py parse /path/to/project

# Build a distributable tarball
python main.py build . --kind tarball --bump patch

# Self-update from ~/Downloads/
python update.py
```

## GUI panels

| Panel | Open with | Purpose |
|---|---|---|
| Node canvas | main window | Pan/zoom dependency graph |
| Inspector | click any node or edge | Imports, exports, definitions, warnings |
| Sidebar RUN | expand "RUN" section | Run/stop scripts from side.project.json |
| Sidebar VERSIONS | expand "VERSIONS" | Archive, compress, apply updates |
| ⚡ PROC | topbar button | Spawn commands, view live stdout/stderr, CPU/RSS |
| LOG | topbar button | Tail logs/s-ide.log in-app |
| 🔨 BUILD | topbar button | Clean/minify/package, view parse-stage timing |

## Parse output

Every parse writes `.nodegraph.json` to the project root containing the full graph
plus per-stage timing data under `meta.perf`. The GUI reads this on load.

## side.project.json

```json
{
  "name": "my-project",
  "version": "0.1.0",
  "description": "",
  "ignore": ["dist", "*.test.py"],
  "run": {
    "dev":   "python main.py",
    "test":  "pytest"
  },
  "versions": { "dir": "versions", "compress": true, "keep": 20 }
}
```

## Logs

```
logs/s-ide.log    ← rotating, 2MB × 5 backups
```

Path is printed to stderr on launch. Tail it:
```bash
tail -f ~/DevOps/s-ide-py/logs/s-ide.log
```
