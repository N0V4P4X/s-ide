# Round 1 — reconcile the uncommitted rewrite, then adopt the round protocol for real

This is the first round run under `AGENTS.md`'s protocol. Read that file and `ROADMAP.md`
first if you haven't. This round is reconciliation, not new features — see
`ARCHITECTURE-DECISIONS.md`'s 2026-08-05 entries for why.

## 1.0 — Inventory and report before changing anything

Run `git status` and `git diff --stat` yourself; don't trust this brief's numbers to still be
current. As of when this brief was written:

- **Uncommitted deletions:** `AGENT_NOTES.md`, `FUTURE.md`, `SELF_IMPROVEMENT.md`, all of
  `ai/` (10 files — `client.py`, `context.py`, `manager.py`, `models.py`, `playground.py`,
  `standards.py`, `teams.py`, `tool_builder.py`, `tools.py`, `workflow_templates.py`, plus
  `AGENT_STANDARDS.md`, `README.md`, `roles/`), all of `build/` (`cleaner.py`, `minifier.py`,
  `packager.py`, `sandbox.py`, `README.md`), all of `monitor/` (`instrument.py`,
  `instrumenter.py`, `perf.py`, `profiler.py`, `README.md`), all of `version/`
  (`version_manager.py`, `README.md`), `migrate.py`, `update.py`.
- **Uncommitted edits:** `.nodegraph.json` (regenerated, ignore its diff), `README.md` (already
  rewritten for the new architecture), `gui/app.html`, `gui/server.py`, `main.py`,
  `parser/project_parser.py`, `run.py`, `test/test_suite.py`.
- **Untracked:** `projects.json`, `web-infra.json`.

Read `git diff main.py run.py parser/project_parser.py` and skim `git diff gui/server.py`
before doing anything else. `main.py`'s diff already shows the intent clearly: the `versions`/
`archive`/`update`/`compress`/`build` subcommands are gone, `serve` is new, and
`cmd_self_check` lost its blank-line-comment style but not its logic. This reads as a
deliberate, mostly-finished pivot — confirm that impression against the rest of the diff
rather than assuming it.

**Write what you find into `ARCHITECTURE-DECISIONS.md` before fixing anything**, specifically:
does every deleted file's functionality have a replacement, or a documented reason it doesn't
need one? The one item flagged in advance (see `ROADMAP.md`) is `ai/teams.py` — a real
turn-based multi-agent engine with role-scoped tool permissions, deleted with no replacement
visible in the diff. If you find others like it, list them. If you conclude the deletions are
clean, say so and why, with specifics — not "looks fine."

## 1.1 — Triage the 3 failing tests

```
python test/test_suite.py -q
```

Currently fails with:

```
FAIL: test_cargo_toml_deps (__main__.TestTomlParser)
  AssertionError: 'serde' not found in []
FAIL: test_generic_toml_keys (__main__.TestTomlParser)
  AssertionError: 'database' not found in ['host', 'port']
FAIL: test_pyproject_tool_detection (__main__.TestTomlParser)
  AssertionError: False is not true
```

`parser/project_parser.py` has a 31-line uncommitted diff — check whether it touches the TOML
parsing path before assuming these are pre-existing. Either fix the parser (if the tests are
right and the code regressed) or fix the tests (if the parser's behavior changed on purpose and
the tests are stale) — don't do neither. If you genuinely can't tell which is correct without
more context, say so in the round's `AUDIT-R1.md` and leave the tests red rather than papering
over them with a skip.

**Acceptance:** `python test/test_suite.py -q` exits 0, with the real output pasted into
`AUDIT-R1.md` — not "tests now pass."

## 1.2 — Fix the CI gap

`.github/workflows/ci.yml` runs:

```
python main.py self-check . --json
```

`main.py`'s `self-check` subcommand (`sp = sub.add_parser("self-check", ...)`, around line 175)
has no `--json` flag — `argparse` rejects it:

```
$ python3 main.py self-check . --json
usage: s-ide [-h] {parse,run,self-check,serve} ...
s-ide: error: unrecognized arguments: --json
```

Pick one and do it:

- Add a real `--json` output mode to `cmd_self_check` (structured pass/fail, suitable for a CI
  artifact), matching the spirit of `--strict-docs` which already exists as a flag on the same
  subcommand.
- Or fix `ci.yml` to call what actually exists (`python main.py self-check .`) if a machine-
  readable mode isn't worth building yet.

Either is fine. Not fine: leaving CI calling a flag that doesn't exist.

**Acceptance:** run whatever `ci.yml` will run, locally, and paste the actual output. If you
add `--json`, show it once with `--strict-docs` too, since `self-check` currently `sys.exit(1)`
on doc health issues under that flag and CI should know which behavior it's getting.

## 1.3 — Write the missing 0.6.0 CHANGELOG entry

`CHANGELOG.md` stops at `[0.5.3] -- 2026-03-19`. `README.md` and `side.project.json` both say
`0.6.0`. Write the entry using `git diff README.md` and the rest of the round's findings as the
source of truth for what actually shipped — headline items per the README diff:

- Frontend rebased from Tkinter to a browser canvas (`gui/app.html`, JS) served by
  `gui/server.py`.
- `ai/`, `build/`, `monitor/`, `version/` subsystems removed (state what replaced them, if
  anything, per 1.0's findings).
- CLI simplified: `versions`/`archive`/`update`/`compress`/`build` removed, `serve` added.
- The MythOS bridge (`/api/nodes`) and n3xu5 infra bridge (`/api/infra`) — confirm from `git
  log` whether these predate this round's uncommitted diff or are part of it, and note which.

Follow the file's existing format (version header, `### Added`/`### Changed`/`### Removed`/
`### Fixed`, semver rationale per the file's own header).

## 1.4 — Git hygiene

Three separate issues, handle them as separate commits:

1. **Tracked-but-gitignored files.** `.gitignore` lists `.nodegraph.json`, `versions/`, and
   `logs/` under "S-IDE specific," but `git ls-files` shows `.nodegraph.json`,
   `.side-metrics.json` (not even in `.gitignore` — add it), and all four `versions/*.tar.gz`
   are still tracked. Untrack them (`git rm --cached`) without deleting the working copies.
2. **`projects.json`** hardcodes `/home/n0v4/DevOps/Native/s-ide`, `/home/n0v4/DevOps/WebDev/
   mythos-os`, `/home/n0v4/DevOps/WebDev/n3xu5/workers/auth` — Nova's actual machine paths.
   This is local registration state, not portable config. Add it to `.gitignore`; don't commit
   it.
3. **`web-infra.json`** is real content `/api/infra` depends on (n3xu5's infrastructure graph,
   consumed by MythOS's `InfraView`). This one should be committed, deliberately, with a commit
   message that says what it is and that it's `/api/infra`'s data source.

## 1.5 — Verify no dangling references to the deleted subsystems

```
grep -rn "^from ai\.\|^import ai\b\|^from build\.\|^import build\b\|^from monitor\.\|^import monitor\b\|^from version\.\|^import version\b" --include=*.py .
grep -n "teams_canvas\|ai/tools\|profiler\|version_manager" gui/app.html gui/server.py main.py
```

Confirm `gui/app.html`'s JS doesn't still call now-gone server routes (the old Teams canvas,
profiler endpoints, etc. — check against `gui/server.py`'s actual route table, which is listed
in a comment near the top of the file). If you find any, either the deletion is incomplete
(fix it) or the reference is dead code on the frontend too (remove it).

## 1.6 — Confirm current bridge API test coverage (report only — fixing it is round 2)

Check whether `test/test_suite.py` has any test hitting `/api/nodes` or `/api/infra` (or the
underlying `_get_nodes`/`_get_infra` handlers in `gui/server.py`) at all. Report what exists,
don't build coverage here — that's round 2's job per `ROADMAP.md`, and round 1 is already
carrying enough. This is purely so round 2's brief can be written accurately instead of
guessing.

## 1.7 — Close the round

1. Run `python main.py self-check .` — must exit 0.
2. Write `tasks/round-1/AUDIT-R1.md` with real command output for every item above.
3. Make sure every decision made rather than followed (1.0's findings especially) is in
   `ARCHITECTURE-DECISIONS.md`.
4. Write `tasks/round-2/00-ROUND-2.md`, expanding `ROADMAP.md`'s round 2 sketch in light of
   what 1.6 actually found.
5. Update `ROADMAP.md`'s status table.
6. Commit (one item, one commit — reconciliation touches enough files that a single commit
   would bury the CHANGELOG/CI/git-hygiene/test-fix decisions from each other).
7. Report to Nova: what passed, what's still red and why (if anything), and specifically
   whether `ai/teams.py`'s deletion looks intentional or accidental — that's the one open
   question this brief can't resolve for you.
8. Stop.
