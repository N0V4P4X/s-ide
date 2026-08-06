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

## 2026-08-05 — round 1 execution decisions (decided, not followed)

Decisions made while executing round 1 that the brief left open or didn't cover. Landed in
seven commits; `tasks/round-1/AUDIT-R1.md` has the command output.

**`self-check --json` was implemented rather than fixing `ci.yml`.** The CI gap was
"`ci.yml` calls `self-check . --json` which doesn't exist" — the brief allowed either. Chosen:
a real machine-readable mode, because CI is the one place a structured pass/fail artifact is
actually useful, and `AGENTS.md` already anticipates extending `self-check`. Semantics: stdout
is pure JSON in `--json` mode; exit code mirrors the human mode exactly (1 on test failure; 1
on doc issues only under `--strict-docs`). CI runs without `--strict-docs`, so it is green
despite this repo having 1 missing / 3 stale READMEs — doc health stays non-fatal by default.

**The "3 failing `TestTomlParser` tests" were left untouched.** They pass in the current tree
and at HEAD; the parser and tests are unchanged across the rewrite, so the failure report was
stale, not a regression. Fixing "nothing to fix" would have meant rewriting green tests.

**The dead Profile/Archive UI was removed, not rewired.** `gui/app.html`'s Profile button
called `/api/profile` (backed by the deleted `monitor/` profiler) and Archive called
`/api/versions/archive` (deleted `version/`). Repointing Profile at the surviving `/api/metrics`
was considered and rejected: `/api/metrics` now only reads XP data written by `/api/xp`, so a
"Profile" button there would show a different feature's data. Removal is the honest
reconciliation. `side.project.json`'s run scripts (`update.py`, `main.py build`) pointed at
deleted commands and were rewritten to the surviving ones.

**`projects/calculator/.nodegraph.json` and `.side-metrics.json` were also untracked.** The
brief listed the root-level artifacts; the same tracked-though-ignored condition applies to the
calculator project's copies. Same class, same treatment.

**Rejected for round 1 (deferred):** making `web-infra.json` generated content, and touching
the `SideNode` shape at all. Both are round 2's job per `ROADMAP.md`.

## 2026-08-05 — round 2: bridge tests are real now; the error contract is decided

**Context:** Round 2's job was to make the MythOS/n3xu5 bridge (`/api/nodes`, `/api/infra`,
`/api/xp`) testable and to decide, test, and document its error behavior. `TestSideNodeAdapter`
previously *simulated* the adapter logic inline and had drifted from `gui/server.py`; round 1's
audit (§1.6) confirmed no test called a real handler and `_get_infra` had zero coverage.

**Chosen (2.0):** tests now call the real handlers. A real `Handler` instance is built with a
stub `_json`/`_error` that captures `{status, body}` — no sockets, no threads, hermetic.
Fixtures are hand-built graph dicts written to a temp dir's `.nodegraph.json`, so the suite no
longer couples to this repo's own live graph. Two server changes were required and are part of
the same commit (2739c36):

- **`_get_infra(infra_path=None)`** — parameterized so tests can point at a temp fixture;
  default is unchanged (`_ROOT_DIR/web-infra.json`), so the `/api/infra` route behavior is
  identical. Pure testability refactor.
- **`_LANG_SKILL_HINTS` re-keyed by extension** (`.py`, `.js`, `.ts`, `.html`, `.css`, `.go`,
  `.rs`, `.sh`, `.md`) instead of language name (`"python"`, ...). This is a **bug fix, not a
  contract change**: the lookup used `ext.lstrip(".")` — `"py"` — which never matched the
  language-name keys, so `.py`/`.js`/`.ts`/`.md`/`.sh`/`.rs` nodes shipped **empty** language
  hints while `README.md` documents `"skillHints": ["python", ...]` for exactly that case.
  Verified against MythOS (`~/DevOps/WebDev/mythos-os/src/lib/calendarBridge.ts`): it reads
  `id/label/detail/kind/category/skillHints/estimateHours/childIds` and its `resolveSkillIds`
  silently drops unmatched hints, so adding the documented `"python"`/`"backend"` hints cannot
  break it. The dict values were otherwise unchanged (markdown stays `["documentation"]`).

**Decided (2.1) — error contract, all five cases keep their current behavior. Tested in
`TestSideNodeAdapter`; the rationale is the contract:**

| Case | Response | Why this stays |
|---|---|---|
| `/api/nodes` with no `root` | 400 `{"error":"root required"}` | Malformed request. MythOS always sends `root`; a missing one is a caller bug, and 400 is the honest signal. |
| `/api/nodes`, root has no `.nodegraph.json` | 200 `[]` | "Project has no graph" vs "empty graph" is functionally identical to a quest importer (nothing to import). An empty array needs zero MythOS special-casing. |
| `/api/nodes`, `.nodegraph.json` corrupt | 200 `[]` | The bridge serves the *cache*, not a live parse. Corruption is self-healing (next `parse` rewrites it) and a cache-management concern, not a contract one. Surfacing it would give MythOS an error it has no recovery path for. |
| `/api/infra`, `web-infra.json` missing | 200 `[]` | Same as nodes: absent tracked infra is an empty import. |
| `/api/infra`, `web-infra.json` corrupt | 500 `{"error":"Failed to load web-infra.json: ..."}` | **Intentional asymmetry.** `.nodegraph.json` is regenerated ephemera; `web-infra.json` is *committed* source that `/api/infra` depends on. A committed file that fails to parse is a defect in this repo and should be loud, not silently empty. MythOS treating non-2xx as "infra view down" is acceptable because the fix is in this repo, not n3xu5. |

A structured 200-with-error envelope for the corrupt-infra case was considered and rejected:
it would add a MythOS-side branch for a case that is a repo bug, and the existing 500 is already
a structured JSON body.

**Decided (2.2) — `SideNode` shape stability rule: additive-only.** New fields may be added;
renames, removals, or type changes to existing fields require an `ARCHITECTURE-DECISIONS.md`
entry plus a coordinated bump in MythOS before landing. Rationale: this matches the existing
stop-condition posture in `AGENTS.md`; a version header would add negotiation to a single-consumer
bridge that currently needs none; and the one known consumer (`calendarBridge.ts`) reads only the
documented fields and ignores unknown ones. No field meaning changed this round. Rule stated in
`README.md`'s "MythOS bridge API" section as well.

**Decided (2.3) — `web-infra.json` stays hand-maintained for now (option 1).** A structural
validation test (`TestWebInfraIntegrity`) now guarantees every `edges[].from/to` and every
`childIds[]` entry references an existing node id — the typo-catching safety net from the
round-2 brief. Option 2 (a generator reading n3xu5's `wrangler.toml`/schema) is proposed-but-not-
built: it crosses repo boundaries and regenerating real infrastructure data on a schedule is a
decision Nova should make, not an agent unilaterally. Revisit if `web-infra.json` drifts from
n3xu5's actual infra again.

**Rejected:** the brief's parenthetical "(see 2.2)" for the infra-path question is a
cross-reference error — the parameterization decision belongs to 2.0 and is documented there.

## 2026-08-05 — round 3: `_collect_external_imports` fast path fixed to the rewrite's graph shape; fast/slow divergence kept by design

**Context:** Round 3.1 found `parser/workspace.py`'s `_collect_external_imports` graph fast path
still stripping the pre-rewrite `ext:` prefix from external edge targets. The rewrite's
`resolve_edges` (committed in `e772b8b`) changed the shape to `target: "ext_<pkg>"` plus a
`externalPackage` field holding the real package name — so the fast path returned
`ext_requests`-style names that never matched a workspace manifest's package keys.
`resolve_project_deps` therefore silently resolved nothing whenever a graph was available.
Introduced in `5dd5f13` and orphaned by the rewrite; no tests covered the fast path, so the
drift went uncaught (round 1 and 2 never exercised `parser/workspace.py` graph-backed paths).

**Chosen:** read `externalPackage` first (it's the authoritative name), with `ext_`/`ext:`
prefix fallbacks for graphs that lack it. The fast path now matches the live graph — verified
against this repo's own `.nodegraph.json` (32 external packages detected, stdlib externals
included). A new test locks the `ext_`+`externalPackage` shape and would fail on the old code.

**Decided (kept, not fixed):** the fast path (graph's `isExternal` edges only) and the scan
path (all `.py` imports) return different sets by design, and that divergence predates the
rewrite. Fast: `ctypes`/`queue`/`tomllib`/`webbrowser` — stdlib modules the graph records as
external edges. Scan: `graph`/`parser`/`process` — this repo's own local packages, which the
scan path cannot distinguish from third-party imports. Neither is "wrong" for its purpose
(graph path = third-party dep resolution; scan path = fallback guess when no graph exists),
and aligning them would require deciding whether local imports count as workspace deps —
not a round-3 call. Flagged for the round-4 consolidation sweep.

**Not a bridge change:** `SideNode` shape untouched; the round-3 brief's "if it touches the
bridge" condition does not fire. `childIds`/`skillHints` matching was instead *confirmed*
against real `parse_project` output by the new 3.2 integration test.
