# S-IDE Agent Standards

This document defines how AI agents operating inside S-IDE should behave. It applies to single-agent chat sessions and multi-agent team workflows equally.

These standards are enforced through the system prompt in `standards.py` and the role prompts in `ai/roles/`. They are not suggestions — an agent that violates them produces output that fails verification.

---

## Core rules (apply to every agent, every role)

### 1. Read before answering
Never describe, summarise, or make claims about code you haven't read in this session. Call `read_file`, `get_file_summary`, or `get_definition_source` first. If the graph overview is enough, call `get_graph_overview`. If not, read the files.

This applies to simple questions too. "What does `parse_project` do?" → call `get_definition_source("parser/project_parser.py", "parse_project")` before answering.

### 2. Show evidence
When citing a problem or making a claim, include the line number, function name, or metric that supports it. "This function has high complexity" is not acceptable. "Line 47, `_resolve_imports`, complexity=14" is.

### 3. Propose before applying
Never write a file without first showing the change and explaining why it is an improvement. The only exception is when the user has explicitly asked the agent to apply changes directly.

### 4. Verify after changes
After writing a file, run the test suite (`run_command("test")`). If tests fail, fix the failure before proceeding. Do not pass a broken state to the next agent.

### 5. Use the session workspace for all agent output
The session workspace (`.side/session/<id>/`) is the shared scratch space where agents communicate. Every agent — including read-only roles — can write here.

```
session/
├── plan/         ← Architect writes here
├── implementation/ ← Implementer writes change summaries here
├── review/       ← Reviewer writes findings and verdict here
├── test/         ← Tester writes results, verdicts, and proposed test cases here
├── docs/         ← Documentarian writes draft docstrings and READMEs here
└── optimization/ ← Optimizer writes benchmarks and reports here
```

Use `write_session_file(path, content)` to write, `read_session_file(path)` to read, `list_session_files()` to see what prior agents wrote.

Source files are promoted from the session workspace to the real project tree only after human approval. The session workspace is never part of the committed codebase.

### 6. Stay in your lane
Each role has permitted tools enforced at dispatch time — a violation returns an error, not silence.

| Role | Can write source files | Can run code | Can write session files |
|---|---|---|---|
| Architect | ✗ | ✗ | ✓ |
| Implementer | ✓ | ✓ | ✓ |
| Reviewer | ✗ | ✗ | ✓ |
| Tester | ✗ | ✓ | ✓ |
| Optimizer | ✓ | ✓ | ✓ |
| Documentarian | ✗ | ✗ | ✓ |

The Reviewer finds problems. The Documentarian drafts docs. Neither touches source directly — that is the Implementer's job, after the others have signed off.

---

## Code quality standards

These are the standards every agent enforces when writing or reviewing Python:

### Structure
- Functions do one thing. If a function has more than 3 responsibilities, split it.
- Cyclomatic complexity ≤ 10. Above that, refactor before proceeding.
- No function longer than 50 lines without a clear justification.
- No nested functions more than 2 levels deep.

### Python specifics
- `from __future__ import annotations` at the top of every `.py` file.
- Type hints on all public function signatures (arguments and return type).
- f-strings, not `.format()` or `%` formatting.
- Dataclasses for fixed-shape data containers, not plain dicts.
- Context managers (`with`) for any resource that needs cleanup.
- No mutable default arguments (`def f(x=[])` is always wrong).

### Naming
- Functions: verb phrases describing what they do (`parse_imports`, not `imports`).
- Classes: noun phrases (`ImportRecord`, not `RecordOfImports`).
- No abbreviations in public names unless universally understood (`i`, `idx`, `cfg` are fine; `prjctPrsr` is not).
- Constants in `UPPER_SNAKE_CASE` at module level.

### Cleanliness
- No commented-out code. Delete it or don't commit it.
- No `TODO` comments in agent-written code — either fix it now or file a plan.
- No `print()` statements in library code. Use the logger.
- No bare `except:` clauses. Always catch specific exception types.

---

## Testing standards

Every function written by an agent must have at least one test. Agents writing code are responsible for writing the tests. The Tester agent augments these; it does not replace them.

### Test naming
`test_<function>_<scenario>` — describes what is being tested and under what conditions.

Good: `test_parse_imports_handles_relative_paths`
Bad: `test_1`, `testImports`, `test_parse`

### Test isolation
- No shared mutable state between test cases.
- Each test creates its own data. Use `tempfile.TemporaryDirectory()` for filesystem tests.
- Tests do not depend on network access, running processes, or environment variables.
- Tests do not depend on execution order.

### Coverage expectations
- Every public function: at least one test for the normal path.
- Every public function: at least one test for an error or edge case.
- Functions that handle external resources (files, processes, network): tests use real temp resources, not mocks, wherever practical.

---

## Architecture rules

These rules govern where code lives and what it can import. They exist to keep the layers clean so the optimizer can work on modules independently.

### Layer order (lower layers know nothing about higher ones)
```
graph/types.py          ← no imports from other s-ide modules
    ↑
parser/*                ← imports graph only
    ↑
monitor/*               ← imports graph only
    ↑
build/*                 ← imports graph, parser
    ↑
process/*               ← no s-ide imports
    ↑
version/*               ← imports parser.project_config only
    ↑
ai/*                    ← imports graph (for context), no gui
    ↑
gui/*                   ← imports everything above
```

Violations: the parser importing from gui, the build pipeline importing from ai, etc. These create circular dependencies that break the optimization pipeline.

### Side effects at the edges
Business logic should be pure functions where possible. File I/O, network calls, subprocess execution, and logging belong at the edges of a module — in the functions that are explicitly about those things — not buried inside data transformations.

### New modules
Every new module gets a `README.md` in its directory before the first commit that adds it. The README describes: what the module does, what it exports, and what it imports from.

---

## Git standards

### Commits
- Atomic: one logical change per commit. A commit that "fixes parsing and adds tests and updates the README" should be three commits.
- Imperative mood: "Add git tool to AI" not "Added git tool" or "Adding git tool".
- Subject line ≤ 50 characters. If you need more, use the body.
- No secrets, API keys, local paths, or user-specific configuration in committed code.

### Branches (when used)
- Feature branches: `feature/<short-description>`
- Fix branches: `fix/<what-is-broken>`
- Merge to main only after tests pass.

---

## Performance standards

These apply when an agent is writing code that will run in a hot path (parsing, canvas rendering, tool dispatch).

- Any new parser stage must be wrapped in a `ParseTimer` context.
- Any function that is called more than once per user interaction should be a candidate for `@timed` instrumentation.
- Canvas redraws are throttled via `_schedule_redraw()`. Never call `_redraw()` directly from a motion or scroll handler.
- Avoid repeated `os.walk` or `ast.parse` calls on the same file in the same pipeline run. Parse once, pass the result.

---

## What makes a good agent output

When an agent completes a task, the output should be:

1. **Correct** — passes all existing tests plus new tests for the change
2. **Minimal** — the smallest change that achieves the goal
3. **Documented** — any new public function has a docstring
4. **Explained** — the agent note explains what was done and why
5. **Reversible** — the change is a clean diff that a human can read, understand, and if needed revert

An output that is "probably fine" is not acceptable in an automated workflow. If the agent is not certain, it should say so in its note and block the handoff until a human reviews.
