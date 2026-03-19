# S-IDE — Future Plan

This document describes where S-IDE is going and why. It is a working document — updated as the project evolves.

---

## What S-IDE is building toward

S-IDE starts as a project graph editor with an AI assistant. The end state is a **development environment that understands code deeply enough to help build, test, and optimize it automatically** — not just read and suggest, but act, verify, and iterate in isolation before a human ever reviews the result.

The path has three phases.

---

## Phase 1 — Understand (current: v0.4.x)

The environment can parse any Python project into a live graph, show data flow between files and functions, and give an AI assistant direct tool access to that graph. The AI can read files, run tests, check metrics, use git, and write files.

**What works now:**
- Single-pass AST parser extracting full data flow (args, return types, calls, raises, complexity)
- Live dependency graph with node cards, bezier edges, minimap
- 14 AI tools including git, file read/write, plan management, sandbox execution
- Syntax-highlighted editor, terminal, process manager — all in one window
- Session persistence (history, viewport, AI conversations) across restarts
- Self-update system with version-sorted tarball selection and rollback

**Remaining Phase 1 work:**
- Shared dependencies across the devspace — packages resolved once at workspace level, copied on build/package

**Completed in v0.4.2–0.4.3:**
- `gui/teams_canvas.py` — Teams canvas designer (⚡ TEAMS topbar button)
- Teams Log tab — live workflow event stream in bottom panel
- `examples/calculator/` — reference project for AI Teams testing
- Manager auto-delegates to canvas when it emits a `run_team` JSON block
- `_on_graph_changed` correctly highlights new/changed nodes after writes

**Completed in v0.4.1:**
- `ai/teams.py` — `TeamSession` engine: turn-based orchestration, session workspace, `WorkflowResult.apply()`
- `ai/playground.py` — isolated Python sandbox for agent code execution (dispatched via `run_in_playground` tool)
- `ai/roles/` — six role definitions with prompts, tool permissions, output paths
- `ai/context.py` — `AppContext` with role-based permissions enforced at dispatch time
- Session workspace model — all agents write to `.side/session/<id>/`; read-only roles use `write_session_file` instead of `write_file`
- 17 tools total; `write_session_file`, `read_session_file`, `list_session_files` added

---

## Phase 2 — Collaborate (complete: v0.4.x)

Phase 2 is done. All components are built and wired.

### What was built

**`ai/teams.py`** — `TeamSession` engine: turn-based orchestration, session
workspace, role-based permissions, `WorkflowResult.apply()`.

**`gui/teams_canvas.py`** — Visual workflow designer on the node canvas.
Toggle with ⚡ TEAMS. Agent cards with role colours, drag-to-reposition,
add/edit/delete, sequence edges. Runs `TeamSession` on the current layout.

**`ai/manager.py`** — Manager: user-facing orchestrator. Surveys the project,
writes a plan, delegates to a team via `run_team` JSON, supports time-limited
bakes, streams to both AI Chat and Teams Log. Includes `scaffold_new_project`.

**`ai/tool_builder.py`** — Self-improving loop: when the Manager calls a tool
that doesn't exist, it pauses, shows an approval dialog, runs a 3-agent team
to build and test the tool, registers it, and resumes. Custom tools persist in
`.side/tools/` across sessions.

**Teams Log tab** — 7th bottom panel tab. Full-verbosity stream of all agent
events with timestamps. Manager tool calls also appear here.

**`examples/calculator/`** — Reference project: PEMDAS-correct GUI + CLI
calculator, 55 tests, ready to load and hand off to an AI team.

### What remains in Phase 2

- **Shared devspace dependencies** — workspace-level package manifest; projects
  reference it, packager copies what's needed into each build output.
- **Saved workflow templates** — save a Teams canvas configuration as a named
  template (e.g. "Standard review: Architect → Implementer → Reviewer → Tester").

## Phase 3 — Optimize (future: v0.6.x+)

The long-term goal: take any well-structured, over-documented, over-logged Python module and produce a **functionally identical version that is orders of magnitude smaller and faster** — automatically, with verification at every step.

### The optimization pipeline

This is the full sequence from verbose dev module to lean published module:

```
1. PROFILE
   Run the module under realistic load with @timed instrumentation.
   Identify the actual bottlenecks — not guesses, measured data.
   Output: ranked list of functions by total time, call count, memory.

2. UNDERSTAND
   For each hot function: extract its full data flow, dependencies,
   side effects, and contracts (what it promises to inputs/outputs).
   The AI reads the source, the graph data, and the test suite.
   Output: per-function specification (what it does, not how).

3. PROPOSE
   The Optimizer agent generates alternative implementations for each
   hot function, targeting: fewer allocations, simpler control flow,
   less I/O, better algorithm complexity.
   Each alternative is annotated with expected improvement and tradeoffs.
   Output: a set of candidate implementations per function.

4. VERIFY
   Each candidate runs against:
   - The existing test suite (must pass 100%)
   - A property-based fuzz harness generated by the Tester agent
   - A timing benchmark comparing old vs. new
   Only candidates that pass all three move forward.
   Output: verified candidates with measured speedup.

5. REDUCE
   Strip everything that was for human understanding, not execution:
   - Docstrings removed (or moved to a separate documentation layer)
   - Logging calls removed or replaced with structured metrics
   - Debug assertions removed
   - Type hints optionally stripped
   - Comments removed
   The minifier already does this mechanically. The optimizer does it
   semantically — it understands what can be removed vs. what is load-bearing.
   Output: stripped module with measured size reduction.

6. BUNDLE
   Combine the optimized, stripped modules into a single output file
   (or minimal set of files) with all internal imports resolved.
   External dependencies are listed, not bundled, unless the user
   requests a fully portable build.
   Output: published package.

7. AUDIT
   Final pass: compare the published package behaviour against the
   original on a held-out test set. Any divergence is a regression.
   If regression found: block publish, surface the failing case to the
   developer with a diff of what changed.
   Output: green/red signal with specific failure context.
```

### Why this is possible with S-IDE's architecture

S-IDE already has:
- Full data flow per function (what it calls, what it reads, what it raises)
- Cyclomatic complexity per function
- Live timing data from `@timed` instrumentation
- A sandbox that runs code in isolation
- A test suite runner
- A minifier that strips docs and comments
- A bundler that resolves internal imports

The optimization pipeline is a structured workflow over tools that mostly already exist. Phase 3 is primarily about connecting them in the right sequence with the right verification gates.

### Shared dependencies across the devspace

A workspace-level dependency manifest lists packages available to all projects. Projects reference them; the packager copies only what's needed into the output. This eliminates the cost of resolving and installing the same packages per-project during development, and keeps the published bundle minimal.

The parser already emits `isExternal` edges for third-party imports — those are the hook for the devspace dependency resolver.

---

## Design principles that guide all of this

**The dev version is for humans. The published version is for machines.**
These are different artefacts with different requirements. S-IDE maintains both.

**Verification before application.**
No agent output touches the real project until it has been verified. Sandboxes, test suites, and property-based checks are not optional.

**The graph is the source of truth.**
Everything the AI knows about a project comes from the parsed graph and the tools. No hallucination about file contents or function signatures — it reads them.

**Modularity is the prerequisite for optimization.**
A well-factored module with clear inputs and outputs and a test suite can be optimized mechanically. A tangled module cannot. The standards enforced in Phase 1 are what make Phase 3 possible.

**Logs are for development. Metrics are for production.**
During development, verbose logging is a feature. In the published package, structured metrics (counts, timings, error rates) replace log lines. The pipeline knows the difference.
