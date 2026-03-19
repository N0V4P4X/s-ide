# S-IDE Changelog

All notable changes follow [Semantic Versioning](https://semver.org):
- **MAJOR** — breaking changes to the project graph format or public API
- **MINOR** — new features (backward compatible)
- **PATCH** — bug fixes and internal improvements

---
## [0.5.3] -- 2026-03-19

### Added
- **Layout mode toggle** (topbar ⊞/⊟ button) -- switch between Compact
  (tighter 280px layer gap) and Spread (340px gap, 80px cluster padding)
  without re-parsing. `_relayout()` rebuilds FileNode stubs from the graph
  dict, re-runs `assign_positions` with updated constants, and calls
  `_fit_view()` to frame the result.
- **Ctrl-click multi-select** -- holding Ctrl while clicking a node toggles
  it in the selection set. Regular click replaces the selection as before.
- **Group drag** -- dragging a node when multiple are selected moves all
  selected nodes together. `_drag` now stores `anchors: {nid: (ox, oy)}`
  for every selected node; `_drag_move` applies the same delta to all.
- **Hover tooltip** -- mousing over a node for 500ms shows a floating panel
  with the node label, file path, category, and line count. Positioned
  near the cursor, stays on-screen. Dismissed on mouse-out, pan, or drag.

---

## [0.5.2] -- 2026-03-19

### Changed
- **Arrow navigation direction** -- canvas arrow buttons (◁ △ ▽ ▷) now move
  the *graph* rather than the camera. Pressing ◁ pushes content leftward,
  consistent with how scrollbars and every map application work.
- **Graph layout: clustered directory layout** -- `parser/layout.py` rewritten.
  Nodes are now grouped by top-level directory (ai/, gui/, parser/, etc.).
  Clusters are arranged in a wide grid (~1.6:1 aspect), sized by population.
  Within each cluster, a layered topo-sort orders nodes left-to-right by
  import depth. Related modules are visually co-located; cross-module arrows
  are long crossing lines that make inter-cluster dependencies obvious.
- **AI chat: collapsible blocks** -- `<thought>` sections become collapsed
  ▶ Thinking… panels (click to expand). Code blocks become collapsible
  ▶ Code / ▶ lang panels with a copy button, open by default. Blocks are
  embedded as `tk.Frame` windows inside the `tk.Text` widget via
  `window_create()`, so they scroll and interact naturally with the rest
  of the conversation.

---

## [0.5.1] -- 2026-03-19

### Added
- **`ai/workflow_templates.py`** -- 5 built-in workflow templates
  (standard_review, quick_implement, full_pipeline, optimize_only,
  docs_update). Save/load/delete user templates persisted to
  `~/.s-ide-templates.json`. Teams canvas right-click menu shows
  a Templates dialog with Load/Save current/Delete buttons.
- **`parser/workspace.py`** -- shared devspace dependency manifest.
  `init_workspace`, `find_workspace_root`, `load/save_workspace`,
  `resolve_project_deps` (uses graph isExternal edges or scans .py files),
  `requirements_txt` generation. Persists as `.side-workspace.json`.
- **301 tests** across 45 classes (added TestWorkflowTemplates: 9,
  TestWorkspaceManifest: 11).

### Fixed
- `_profile_btn` not initialised in `SIDE_App.__init__` -- AttributeError
  on first load before the topbar was built.
- `profile_project` missing from `ALL_TOOLS` and `ROLE_TOOLS` -- agents
  could not call it even though the schema and handler existed.
- Manager prompt: added `git pull` and a dedicated Profiling section
  directing the Manager to use `profile_project` instead of `@timed`.

---

## [0.5.0] -- 2026-03-19

### Added
- **`monitor/profiler.py`** -- cProfile-based live project profiler.
  Profiles a project entry point in a subprocess sandbox, parses pstats,
  writes `.side-metrics.json` so node overlays update immediately.
- **`profile_project` tool** -- Manager can call `profile_project()` to
  trigger profiling. Results in AI Chat and on node cards.
- **Git tool expanded** -- 22 commands incl. add_all, commit_all, push,
  pull, checkout_new, diff_staged, blame, init, remote, reset, tag.
  Structured params: message, remote, branch, n.
- **Profile button** in topbar -- auto-detects entry point, shows top
  10 functions, updates node overlays via MetricsWatcher.
- **280 tests** (added TestProfiler: 10, TestGitToolExpanded: 8).

---

## [0.4.5] -- 2026-03-19

### Added
- **Session browser** in Teams Log tab -- split pane: past sessions listed
  on the left (ID + age), click to load TASK.md, plan, review, and test
  reports into the log view. Refreshes automatically when a project loads
  or the graph changes. 'Sessions' button in the header for manual refresh.

### Fixed
- **Message trimming** -- Manager history capped at 32 messages (system +
  last 30) before each turn. Prevents context overflow in long bakes.
- **Nested ToolMissingError guard** -- `_do_turn_depth` counter prevents
  infinite recursion if a resumed turn hits another unknown tool (max 3).
  Depth tracked in `_do_turn`, reset in `_run_turn`, decremented in finally.

### Documentation
- `ai/README.md` fully rewritten: `manager.py` and `tool_builder.py` sections
  added with usage examples, delegation protocol, custom tool format.
- `FUTURE.md` Phase 2 marked complete; remaining Phase 2 items listed.
- Stray `gui/teams_canvas.py.README.md` removed.

---

## [0.4.4] -- 2026-03-19

### Added
- **Self-improving tool creation** (`ai/tool_builder.py`) -- when the Manager
  calls a tool that does not exist, the agent workflow pauses, shows an approval
  dialog, then runs a 3-agent team (Architect + Implementer + Tester) to build
  and test it. On success the tool is registered and the Manager resumes.
- **Custom tool registry** -- `register_custom_tool()`, `load_all_custom_tools()`,
  `get_custom_schemas()`, `dispatch_custom()`. Tools persist in `.side/tools/`.
- **Manager `on_log` callback** -- Manager text and tool calls stream to the
  Teams Log with `[HH:MM:SS] [Manager]` prefix, not just AI Chat.
- **Hardened prompts** -- base prompt explicitly blocks fake commands;
  Manager prompt mandates: bake = always delegate, survey first.
- **258 tests** across 41 classes (added `TestToolBuilder`: 9 tests).

### Fixed
- `Manager` missing from `gui/app.py` imports -- `NameError` on first send.
- `ToolMissingError.args` renamed to `tool_args` to avoid `Exception.args` collision.

---

## [0.4.3] — 2026-03-19

### Added
- **`examples/calculator/`** — S-IDE example/test project: PEMDAS-correct GUI
  and CLI calculator. Recursive-descent parser with no `eval()`. 55 tests
  covering all PEMDAS rules, edge cases, division-by-zero, mismatched parens.
  Tkinter GUI with live result preview; CLI with REPL, single-expression, and
  pipe modes. Ready to load in S-IDE and hand off to an AI team.
- **Teams Log tab** — 7th bottom panel tab. All workflow events streamed in
  real time with timestamps. Verbose events (tool calls, text chunks) stay here
  only; handoffs/starts/completions/errors also appear in AI Chat. Workflow
  start auto-switches to this tab.
- **`_on_graph_changed` fix** — `_after_parse` was defined but never called;
  now hooked with `self.after(150, _after_parse)` so node highlights fire
  correctly after file writes during bake/team sessions.
- **Auto team-canvas population** — `_log_team_event` now reads the Manager's
  `run_team` JSON, populates the Teams canvas with the specified agents,
  writes the task to the Plan tab, switches to Teams mode, and fires the
  workflow automatically.
- **249 tests** across 40 classes (added `TestTeamsLog`, `TestManagerScaffold`,
  `TestCalculatorExample`).

### Fixed
- `examples/calculator/src/evaluator.py` duplicate removed (pemdas.py is
  the canonical evaluator; evaluator.py was a pre-existing stub).
- Teams Log `_teams_log_append` is now safe to call from any thread (uses
  `self.after(0, ...)` for thread-safe Tk updates).

---

## [0.4.2] — 2026-03-19

### Added
- **`gui/teams_canvas.py`** — `TeamsCanvasMixin`: full AI Teams canvas designer.
  Toggle with **⚡ TEAMS** in the topbar. Agent cards with role colours, drag-to-
  reposition, +  button to add agents, double-click to configure, right-click menu.
  Routes all canvas events (click, drag, double-click, right-click) to teams-mode
  handlers when active.
- **`_tw_run_workflow()`** — builds a `TeamSession` from the current canvas layout,
  orders agents by edge topology, runs async, shows progress in AI Chat tab, opens
  result dialog with **Apply to Project** button.
- **`_tw_show_add_dialog()`** — role/model/name picker with auto-name from role.
- **`_tw_show_result_dialog()`** — review dialog: summary, session path, approve/reject.
- **Plan tab** — now shows task description text + **▶ Run Workflow** button.
- **230 tests** (added `TestTeamsCanvas`: 16 tests for mixin logic without Tk).

### Changed
- `gui/teams_canvas.py` — tkinter imported with try/except so the module is
  importable in headless test environments.

---

## [0.4.1] — 2026-03-19

### Added
- **`ai/teams.py`** — `TeamSession` engine: turn-based multi-agent workflow
  orchestration with `AgentConfig`, `WorkflowResult`, `TeamEvent`. Each agent
  runs in sequence, communicates via the session workspace, and hands off via
  `write_agent_note`. `WorkflowResult.apply()` promotes approved outputs to the
  real project tree.
- **`ai/playground.py`** — `Playground`: isolated Python execution sandbox.
  Hard-links project into a temp dir, runs snippets with a 10s timeout, deletes
  sandbox after. Dispatched via `run_in_playground` tool.
- **`ai/roles/`** — Six role definitions with prompts, tool permissions, and
  expected session workspace output paths: Architect, Implementer, Reviewer,
  Tester, Optimizer, Documentarian.
- **Session workspace** — `.side/session/<id>/` is the shared scratch area all
  agents write to. `write_session_file`, `read_session_file`, `list_session_files`
  tools added (tool count: 17).
- **Role-based permissions** — `AppContext.can_use(tool)` checks role permissions
  at dispatch time. Read-only roles (Reviewer, Documentarian, Architect) cannot
  call `write_file` — they use `write_session_file` instead.
- **214 tests** across 37 classes (added `TestTeamSession`, `TestPlayground`).

### Documentation
- `ai/README.md` — fully rewritten: correct tool table, permission matrix,
  session workspace layout, teams/playground/roles usage
- `FUTURE.md` — Phase 1 remaining work updated; Phase 2 starting point clarified
- `ai/AGENT_STANDARDS.md` — session workspace model documented with role table

---


## [0.4.0] — 2026-03-18

### Fixed
- **Typing in AI/terminal** — `focus_force()` replaces `focus_set()`; canvas
  set `takefocus=False` so clicks never steal keyboard focus from input fields
- **`_build_run_panel` crash** — `_run_chevron` was `None` at bind time;
  widget now created unconditionally at panel build
- **`gui/__init__.py`** — removed eager `from .app import …` which was pulling
  tkinter into every test that touched any `gui.*` submodule
- **`gui/markdown.py`** extracted — markdown rendering now importable without
  a display, enabling headless tests

### Added
- **14 AI tools** — `read_file`, `list_files`, `get_file_summary`,
  `search_definitions`, `get_graph_overview`, `get_metrics`, `run_command`,
  `get_definition_source`, `write_file`, `create_plan`, `update_plan`,
  `write_agent_note`, `run_in_playground`, `git`
- **Multi-select filter chips** — any combination of file types; docs/config
  hidden by default; ALL resets; chip dims when hidden
- **Doc→file dashed links** — README nodes draw dashed connectors to every
  source file in the same directory when docs are visible
- **184 tests** across 33 classes including markdown, session state, git tool,
  filter logic, doc-link directory matching

### Changed
- `show_ext = False` by default (external deps hidden until explicitly toggled)
- `hidden_cats = {"docs", "config"}` by default
- PROC/LOG/BUILD/AI buttons removed from topbar (now bottom panel tabs)

---

## [0.3.x] — 2026-03-17 to 18

### Added
- **Bottom panel** — PanedWindow with drag-resize; tabs: Projects, AI Chat,
  Plan, Playground, Editor, Terminal
- **AI chat** — Ollama integration, streaming responses, markdown rendering
  (headers, bold, italic, code blocks, bullets, numbered lists)
- **Syntax-highlighted editor** — `gui/editor.py`; Python/JS/JSON/shell;
  find/replace, line numbers, read-only toggle, save triggers re-parse
- **`gui/panels.py`** — all tab content extracted from `app.py`
- **`gui/state.py`** — `SessionState` persists projects, AI history, terminal
  history, viewport, bottom panel height/tab across restarts
- **Canvas double-click** → open editor; right-click → context menu
- **`ai/standards.py`** — hidden dev standards in every AI system prompt

---

## [0.2.0] — 2026-03-17

### Added
- **Data flow extraction** — `Definition` extended with `args`, `return_type`,
  `calls`, `raises`, `complexity`, `end_line`
- **Single-pass Python parser** — `_SinglePassVisitor` replaces three separate
  `ast.walk()` calls; ~30% faster on large files
- **`@timed` instrumentation** — `monitor/instrumenter.py` adds timing
  decorators to public functions; backup + rollback support
- **Sandbox runner** — `build/sandbox.py`; clean and minified modes;
  logs retained to `logs/sandbox/<timestamp>/`

---

## [0.1.x] — 2026-03-17

### Added
- Project graph parser (Python, JS/TS, JSON, TOML/YAML, shell)
- Canvas node editor — drag, pan, zoom, minimap, inspector
- Process manager, version manager, build pipeline (clean/minify/package)
- Self-update via `update.py`
