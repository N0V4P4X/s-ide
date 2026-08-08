# S-IDE — v0.6.0

**Systematic Integrated Development Environment** — a project graph parser and visualizer built in Python.

S-IDE parses any Python/JavaScript/JSON/Shell project into a live dependency graph, renders it as an interactive node canvas in the browser, and lets you inspect, navigate, and edit your codebase visually.

---

## Architecture

```
s-ide/
├── gui/
│   ├── app.html               # Full JS frontend (Canvas rendering, editor, terminal, git)
│   └── server.py              # HTTP server + API bridge (Python)
├── parser/                    # Project analysis pipeline
│   ├── project_parser.py      # Orchestrator: walk→parse→edges→layout→audit
│   ├── parsers/               # python, js, json, shell, toml/yaml
│   └── workspace.py           # shared devspace dependency manifests
├── graph/types.py             # FileNode, Edge, ProjectGraph, Definition
├── process/                   # Subprocess lifecycle management
├── bin/                       # Graph tooling (relay-graph.py → plans.graph.json)
├── examples/calculator/       # Reference project: PEMDAS GUI + CLI calc
├── test/test_suite.py         # Unit tests (stdlib unittest)
├── main.py                    # CLI entry point
├── run.py                     # Launcher (server + browser)
└── CHANGELOG.md
```

---

## Quick start

```bash
python run.py                          # start server, open browser
python run.py --project ~/my-project   # pre-load a project
python main.py parse ~/my-project      # parse only, no server
python test/test_suite.py             # run tests
```

---

## GUI layout

```
┌──────────────────────────────────────────────────────────────┐
│ TOPBAR  logo · project · [Graph] · ↺ Re-parse · ⊞ Fit      │
├───────────────────────────────────────────────┬──────────────┤
│  CANVAS                                       │  INSPECTOR   │
│  • node cards (one per source file)           │  (on click)  │
│  • bezier import edges                        │              │
│  • dashed doc→source links                   │              │
├───────────────────────────────────────────────┴──────────────┤
│  ▓ resize handle                                              │
├───────────────────────────────────────────────────────────────┤
│  Editor │ Terminal │ Plan │ Git                               │
└───────────────────────────────────────────────────────────────┘
```

---

## Git integration

The Git tab provides quick-access buttons for common workflows: status, diff, log, add all, commit, push, pull. All operations run as subprocess calls against the project's git repository.

---

## Parser pipeline

Each stage is timed and stored in the graph JSON under `meta.perf`:

1. **init_project_config** — load/create `side.project.json`
2. **walk_directory** — discover all source files
3. **per-file parsing** — call the appropriate language parser (Python AST, JS regex, JSON, Shell, TOML/YAML)
4. **resolve_edges** — turn raw import strings into graph edges
5. **assign_positions** — auto-layout for the node editor
6. **audit_docs** — README / empty-module health check
7. **write_graph_json** — auto-save `.nodegraph.json`

---

## License

GPL-3.0-or-later. See `LICENSE.txt`.

---

## MythOS bridge API

The `/api/nodes` endpoint exports graph nodes as `SideNode`-shaped JSON for consumption by [MythOS](../../WebDev/mythos-os/) via `calendarBridge.sideNodeToQuest()`.

```
GET /api/nodes?root=&category=&lang=
```

**Query params:**
| Param | Required | Description |
|---|---|---|
| `root` | yes | Project root path with a parsed graph (a `.nodegraph.json` next to it) |
| `category` | no | Override category for all nodes |
| `lang` | no | Override skill hints (e.g. `python`) |

**Response:** Array of `SideNode` objects:
```json
{
  "id": "side:agent_loop_py",
  "label": "agent_loop.py",
  "detail": "File: agent_loop.py\n130 lines, 8 imports, 0 exports, 5 definitions",
  "kind": "task",
  "category": "python",
  "skillHints": ["python", "time", "subprocess", "os"],
  "estimateHours": 2.6,
  "childIds": ["side:time", "side:subprocess"],
  "_source": { "project": "/path/to/project", "path": "agent_loop.py" }
}
```

**Integration:** MythOS `SideImporter` component auto-connects to `localhost:7700` and fetches nodes from this endpoint. Each node is converted to a MythOS quest with skill-tree placement and XP weighting derived from `estimateHours`.

**Stability rule (additive-only):** The `SideNode` shape is a cross-repo contract — MythOS's `calendarBridge.sideNodeToQuest()` consumes it. New fields may be added freely; renaming, removing, or changing the type of an existing field requires an `ARCHITECTURE-DECISIONS.md` entry and a coordinated change in MythOS before landing. Consumers should ignore unknown fields. Error cases return either a 400/500 with a `{"error": ...}` body (malformed request / committed-data defect) or an empty array for "nothing to import" (no graph, no infra file) — an empty import is not an error.

---

## Web Infrastructure graph

The `/api/infra` endpoint serves hand-authored infrastructure graphs as importable SideNode-shaped JSON. Source data: committed graph files at the repo root, selected by name.

```
GET /api/infra?graph=web-infra
```

**Query params:**
| Param | Required | Description |
|---|---|---|
| `graph` | no | Which committed graph file to serve. `web-infra` (default) → `web-infra.json`, `relay` → `relay.graph.json`, `plans` → `plans.graph.json`. Unknown names return a structured 404 `{"error": "unknown graph: <name>"}`. |

**Response:** Array of nodes (workers, databases, buckets, domains, services, external deps) with relationships. Each node carries the `SideNode` shape plus an additive `edges` field listing its **outgoing** typed edges as `{from, to, type}` (ids `infra:`-prefixed). The union of every node's `edges` is the full edge set — hierarchy via `childIds`, flow via `edges`.

**Components tracked (web-infra):** homepage worker, n3xu5-auth worker, email-gate worker, D1 database, R2 bucket, rate limits, secrets, domains (n0v4-n3xu5.art, n3xu5.art, forsythzines.art), SSO IdP, email routing, calendar API, Resend, Porkbun, C2-Panel.

**MythOS integration:** `InfraView` component shows infrastructure health dashboard with import-as-quest capability.
