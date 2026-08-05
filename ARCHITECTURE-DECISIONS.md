# S.I.D.E. — architecture decisions

Running log. What was picked, what was rejected, why. **Read this before re-deciding something
that was already decided once.** Append; don't rewrite history.

Canonical project-level notes live in Nova's vault at
`~/Documents/Vault/20-projects/s-ide.md`. This file is the in-repo summary an agent needs.

---

## 2026-08-05 — adopt the n3xu5/mythos-os round convention here too

**Context:** S.I.D.E. started as a personal dev tool. It isn't one anymore — MythOS's Map
mechanic is built directly on its `SideNode` export (`/api/nodes`, consumed by `SideImporter`
+ `calendarBridge.sideNodeToQuest()`), and `/api/infra` is now how the n3xu5 infrastructure
graph gets into MythOS's `InfraView`. Two other repos depend on a contract this one exports,
which is exactly the situation n3xu5 and mythos-os were already run under: rounds, a decision
log, an audit gate, `opencode.json` permission scoping.

**Chosen:** `AGENTS.md`, `ROADMAP.md`, `ARCHITECTURE-DECISIONS.md` (this file), and
`opencode.json` now exist here, matching the shape used in `~/DevOps/WebDev/n3xu5` and
`~/DevOps/WebDev/mythos-os`. `main.py self-check` is designated the audit gate — the
equivalent of `scripts/audit.mjs` in the other two repos — since it already runs tests, parses
the project, and audits docs in one command.

**Why:** Mirrors the call the MythOS decision log already made for itself on 2026-08-01
("one system across both repos, already battle-tested, and Nova already knows how to drive
it"). No reason for the third repo in the same dependency chain to run on a different process.
Also overdue by the numbers: `bin/devops-status.sh`'s 2026-07-26 scan flagged `Native/s-ide`
at 46 uncommitted files, and it was still an unactioned "[next up]" in the 2026-07-31 and
2026-08-02 daily checkins — this has been sitting for over a week, not discovered today.

**Rejected:** writing a new, S.I.D.E.-specific process instead of reusing the existing one.
The tool doesn't have a reason to diverge — it's Python instead of TypeScript, but the round
shape (work → self-audit → write the next round → stop) doesn't care about language.

**Revisit when:** never, unless the round protocol itself changes in n3xu5/mythos-os, in which
case bring the change here too rather than letting the three repos drift apart.

## 2026-08-05 — round 1 fixes the tree before building anything new

**Context:** Before scaffolding rounds, the tree was checked. It has a large uncommitted diff
(the deletion of `ai/`, `build/`, `monitor/`, `version/` and three top-level docs, plus real
edits to `main.py`, `gui/server.py`, `gui/app.html`, `parser/project_parser.py`, `run.py`,
`test/test_suite.py`), 3 failing tests in `TestTomlParser`, a CI workflow that calls a CLI flag
(`self-check . --json`) that doesn't exist, and a `CHANGELOG.md` that stops two minor versions
behind what `README.md` and `side.project.json` both claim.

**Chosen:** Round 1 is reconciliation only — inventory, triage, fix, land, document. No new
features, no API hardening (that's round 2), until `self-check` can pass and CI can run.

**Why:** Building round 2's bridge-hardening work on a tree that can't currently pass its own
tests would mean debugging two things at once. This is the same "harvest before dispatch"
lesson mythos-os already paid for once — a bounded task is only bounded if its dependencies
compile (or, here, if its test suite is green).

**Not yet decided, flagged for round 1 to surface rather than silently resolve:** whether the
`ai/`/`build/`/`monitor`/`version` deletion is fully intentional across every file, or whether
some of it (particularly `ai/teams.py`'s multi-agent orchestration) is being lost by omission
rather than by decision. Round 1's job is to report this clearly, not to guess.

## 2026-08-05 — round 1: inventory of the uncommitted v0.6.0 rewrite

**Context:** Round 1's first job was to inventory the uncommitted working tree before touching
anything — staged deletions of `ai/`, `build/`, `monitor/`, `version/`, `migrate.py`,
`update.py`, `AGENT_NOTES.md`, `FUTURE.md`, `SELF_IMPROVEMENT.md`, plus rewrites of `main.py`,
`run.py`, `gui/server.py`, `gui/app.html`, `parser/project_parser.py`, `test/test_suite.py`,
`README.md`. `git status`/`git diff --stat` were run; numbers below are from the actual diff.

**Findings — every deletion and what replaces it:**

| Deleted | ~lines | Replacement |
|---|---|---|
| `ai/` (10 modules + `roles/`, ~5,200) | 5,200 | None in-tree. The GUI's Ollama chat/tool/teams surface (`/api/ai/chat`, `/api/ai/cancel`, `/api/tool`, the AI tab) was removed from `gui/server.py` and `gui/app.html`. OpenCode now drives this repo directly via the round protocol (`AGENTS.md`) — the in-repo agent is dead by decision, not accident. **Exception flagged for Nova:** `ai/teams.py` has no replacement. |
| `build/` (cleaner, minifier, packager, sandbox) | 1,650 | None. `main.py build` removed; nothing consumed the packager/minifier after the GUI stopped offering them. Git now serves the release/versioning role that packaging partly served. |
| `monitor/` (instrument, instrumenter, perf, profiler) | 1,900 | `monitor.perf.ParseTimer` was **inlined** into `parser/project_parser.py` — per-stage parse timing still ships under `meta.perf`, so that functionality survived the deletion. Instrumenter/profiler did not survive; `/api/metrics` remains in `gui/server.py` but nothing in-tree generates `.side-metrics.json` anymore (only `/api/xp` writes it). |
| `version/` (version_manager) | 430 | None. `versions`/`archive`/`update`/`compress` subcommands removed; the four `versions/*.tar.gz` snapshots stay as history but nothing manages them. |
| `migrate.py`, `update.py` | 645 | One-shot v0.5→v0.6 Tkinter→web migration tooling; the migration itself is committed (f70af14 et seq.), so the scripts have no future role. |
| `AGENT_NOTES.md`, `FUTURE.md`, `SELF_IMPROVEMENT.md` | 226 | Superseded by `AGENTS.md`/`ROADMAP.md` (the round protocol) and this decision log. |

**Chosen:** land the deletion as staged. It is coherent — nothing in the new tree imports the
deleted modules (`grep` for `from ai|build|monitor|version` is clean), and code, tests, and
README were all rewritten to match it in the same working tree. Committing it is reconciliation
of already-in-motion state, which `AGENTS.md` stop condition 4 authorizes round 1 to do.

**Flagged for Nova, not decided here:** `ai/teams.py` (460 lines — `TeamSession`, turn-based
multi-agent orchestration with role-scoped tool permissions, sandboxed per-agent copies, and a
human-approval gate before output is applied). Its deletion looks *consequential* (it went out
with the rest of `ai/`), not specifically *intentional*: nothing in the rewrite mentions teams,
and the round protocol's agent is a single OpenCode instance, not a team. If it should be
preserved or ported, it is recoverable from git (`git show HEAD:ai/teams.py`); round 4's plan
already reserves a slot for deciding its fate for real.

**Also found during inventory (handled as separate round-1 items):**
- The "3 failing `TestTomlParser` tests" in the brief are stale — all 4 TOML tests pass, both in
  this tree and at HEAD (they are unchanged across the rewrite). No code change was needed.
- The MythOS bridge (`/api/nodes`, `/api/infra`) has **zero committed history** — both handlers
  and the "MythOS bridge API"/"Web Infrastructure graph" README sections are entirely part of
  the uncommitted rewrite. `web-infra.json` is new and untracked.
- `gui/app.html` still shipped two buttons calling removed routes (`/api/profile`,
  `/api/versions/archive`); removed this round (item 1.5).
