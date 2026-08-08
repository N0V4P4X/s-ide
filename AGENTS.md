# S.I.D.E. — agent instructions

You're working on S.I.D.E.: a project graph parser and visualizer. It walks a Python/JS/
JSON/Shell project, builds a dependency graph, and serves an interactive node canvas in the
browser (`gui/app.html`, JS, talking to `gui/server.py`, Python). No framework, no build step
on the frontend, stdlib `unittest` for tests.

**This is no longer a personal tool — read this before touching the bridge endpoints.**
`GET /api/nodes` and `GET /api/infra` are a real cross-repo contract. MythOS's `SideImporter`
and `InfraView` (`~/DevOps/WebDev/mythos-os`) fetch from `localhost:7700` and depend on the
exact `SideNode` shape documented in `README.md`. Changing that shape without saying so in
`ARCHITECTURE-DECISIONS.md` breaks a repo you can't see from here.

Same conventions as `~/DevOps/WebDev/n3xu5` and `~/DevOps/WebDev/mythos-os` — rounds,
kickoffs, self-audits, a decision log. If you've worked either of those repos, you already
know the shape of this one. Canonical project-level notes live in Nova's vault at
`~/Documents/Vault/20-projects/s-ide.md`; this file and `ARCHITECTURE-DECISIONS.md` are the
in-repo summary an agent needs, not the full record.

## Graphs first — read the graph files, not the prose

The hand-authored and generated graphs are the clinical index for agents. A session that
reads `relay.graph.json` or `plans.graph.json` knows in one file what would otherwise take
six reads and a lot of prose to infer. **Every session that needs the n3xu5 orchestration
picture starts by reading the graphs, not the markdown:** prose is for Nova, graphs are for
agents. Same facts, two renderings.

- `web-infra.json` — the n3xu5 cloud stack (workers, D1, R2, domains), hand-authored,
  served via `/api/infra?graph=web-infra`.
- `relay.graph.json` — who did what in what order: Opus → subsystems → daily jobs/gates,
  with `dispatches` / `reports-to` / `unblocks` / `blocks` / `corrects` edges. Hand-authored.
- `plans.graph.json` — tiers, stages, and blocking across every vault project/task note.
  **Generated** by `bin/relay-graph.py` from Vault front matter; never hand-edit it.

When a task touches orchestration, read `relay.graph.json` first and `plans.graph.json`
second; go to the prose only for detail the graphs don't carry.

## Current round: round 1

Start at `tasks/round-1/00-ROUND-1.md`. It exists because the repo is mid-rewrite and the
tree currently doesn't pass its own CI — read that file before writing any new code. Round 1
is reconciliation, not new features.

## The round protocol — this is how you run unattended

Nova will say "continue" and expect a full round each time. One round = one pass through this
loop. **Do not start the next round without being told to continue.**

```
1.  Read this file, then ROADMAP.md, then tasks/round-N/00-ROUND-N.md.
2.  Work the items IN ORDER. Commit each item separately.
3.  Run `python main.py self-check .`. It must exit 0.
4.  Write tasks/round-N/AUDIT-RN.md — with ACTUAL command output pasted in, not summaries.
5.  Append an entry to ARCHITECTURE-DECISIONS.md for anything you decided rather than followed.
6.  Write tasks/round-(N+1)/00-ROUND-(N+1).md, ADAPTED to what you actually found.
    ROADMAP.md has the brief; your job is to expand it in light of this round's reality.
    If this round changed what the next one should be, change it and say why.
7.  Update the status table in ROADMAP.md.
8.  Commit. Report: what passed, what didn't, what you changed about the plan, and whether
    any stop condition fired.
9.  STOP.
```

**"Implemented" is not a result.** For every acceptance criterion, show the command you ran
and its actual output. This rule is inherited from n3xu5/mythos-os and exists because
`main.py self-check .` currently fails outright — three tests are red on the tree right now,
and nobody would know that from reading the code.

## Stop conditions — halt and wait for Nova

Do not work around these. Do not do them and flag it afterward. Stop, explain, wait.

1. **Any new dependency.** This project is stdlib-only on the Python side and framework-free
   on the JS side, deliberately (see `README.md`'s architecture). Adding one — `pip install`
   anything, a JS framework, a bundler — needs a human.
2. **Any change to the `SideNode` JSON shape returned by `/api/nodes` or `/api/infra`**,
   including field renames, removals, or type changes. MythOS reads this contract from another
   repo you cannot see the impact in. Propose the change in `ARCHITECTURE-DECISIONS.md` and
   stop; landing it is Nova's call, same as every cross-repo break in n3xu5/mythos-os.
3. **Any real network call, credential, or API key.** There are none in this repo today;
   keep it that way unless told otherwise.
4. **Deleting or restoring the `ai/`/`build/`/`monitor`/`version` subsystems.** Round 1 finds
   them already staged for deletion, uncommitted. Reconciling that state (committing the
   deletion, or reverting it) is Round 1's job *because it's already in motion* — but reviving
   any of them later, or deleting something not already staged, needs a decision written down
   first, not just done.
5. **The same audit check (`self-check`) failing twice in a row for the same reason.** That
   means the plan is wrong, not the code. Say so rather than trying a third approach.
6. **`git push`.** Check `git remote -v` and `git push --dry-run origin main` yourself before
   assuming either direction; this tree already has substantial real uncommitted work in it —
   see Round 1.

## Non-negotiables

**The bridge API is a contract, not an implementation detail.** `/api/nodes` and `/api/infra`
export `SideNode`-shaped JSON per `README.md`'s "MythOS bridge API" section. Two other repos
depend on the exact shape. Treat any change to it like a public API break.

**GPL-3.0-or-later.** `LICENSE.txt` is the whole license; don't add code under an incompatible
license.

**Match the existing style.** Stdlib Python, no framework JS, dependency-free where the
project already sets that precedent. If a task seems to need a new library, that's a stop
condition (above), not a `pip install`.

**Every architecture decision — including ones that turn out to be mistakes — gets written to
`ARCHITECTURE-DECISIONS.md`.** Read the whole file before re-deciding something already
decided once.

## Repo layout, quick reference

- `main.py` — CLI entry point: `parse`, `run`, `serve`, `self-check`.
- `run.py` — launcher (starts `gui/server.py`, opens the browser).
- `gui/server.py` — HTTP server + API bridge (Python stdlib `http.server`). All `/api/*`
  routes are listed at the top of the file.
- `gui/app.html` — the entire JS frontend: canvas rendering, editor, terminal, git panel.
  Single file, no build step.
- `parser/` — the analysis pipeline. `project_parser.py` orchestrates walk → parse → edges →
  layout → audit; `parser/parsers/` has one module per language (python, js, json, shell,
  toml/yaml).
- `graph/types.py` — `FileNode`, `Edge`, `ProjectGraph`, `Definition`.
- `process/` — subprocess lifecycle management (used by the Processes tab and `run` command).
- `examples/calculator/` — reference project used by tests and manual QA.
- `test/test_suite.py` — the whole test suite, stdlib `unittest`, currently 134 tests green
  (see `python main.py self-check .`).
- `relay.graph.json` — hand-authored orchestration graph (Opus → subsystems → jobs/gates),
  served via `/api/infra?graph=relay`. Committed.
- `plans.graph.json` — generated plans graph (Vault → tiers/stages/blocking), served via
  `/api/infra?graph=plans`. Generated by `bin/relay-graph.py`; never hand-edit.
- `bin/relay-graph.py` — regenerates `plans.graph.json` from Vault front matter
  (`20-projects/` + `70-tasks/`). Stdlib; run from repo root.
- `side.project.json` — this project's own metadata (name, version, `run` scripts) in the
  format S.I.D.E. itself uses to register any project, including this one.
- `projects.json` — locally registered projects (this repo, `mythos-os`,
  `n3xu5/workers/auth`). Hardcodes an absolute local path — see Round 1 on whether this
  belongs in git at all.

## Known traps (as of round 1's start)

- **The tree is mid-rewrite and uncommitted.** `git status` shows `ai/`, `build/`, `monitor/`,
  `version/` and several top-level docs staged as deletions, plus real edits to `main.py`,
  `gui/server.py`, `gui/app.html`, `parser/project_parser.py`, `run.py`, `test/test_suite.py`
  — none of it committed. `README.md` already describes the post-rewrite architecture; the
  code and tests haven't fully caught up. Do not assume a clean `git diff` means nothing
  changed — check `git status` yourself.
- **CI is currently broken as written.** `.github/workflows/ci.yml` runs
  `python main.py self-check . --json`; `self-check` has no `--json` flag. This has presumably
  never actually run green since the flag was added to the workflow.
- **3 pre-existing test failures**, all in `TestTomlParser`: `test_cargo_toml_deps`,
  `test_generic_toml_keys`, `test_pyproject_tool_detection`. Not yet triaged as caused by the
  rewrite or older.
- **Files tracked in git despite matching `.gitignore`:** `.nodegraph.json`,
  `.side-metrics.json`, all of `versions/*.tar.gz`. The ignore rules were added after these
  were already committed, so they keep showing as modified on every parse/profile run.
- **`CHANGELOG.md` stops at 0.5.3.** `README.md` and `side.project.json` both say 0.6.0. No
  entry documents what actually changed.

## Testing

```
python test/test_suite.py -q      # unit tests
python main.py self-check .       # tests + parse + doc audit — the audit gate
```

`self-check` is this repo's equivalent of `scripts/audit.mjs` in n3xu5/mythos-os: every round
after round 1 self-certifies against it. If you extend it (new checks, the `--json` flag CI
needs), that's exactly the kind of thing that belongs in `ARCHITECTURE-DECISIONS.md`.
