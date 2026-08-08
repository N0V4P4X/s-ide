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

## 2026-08-06 — S-IDE's node model converges on MythOS's Map spec, not a separate invention

**Context:** The UX-rebuild scoping (below) surfaced the question of what a "node" is allowed
to be once it's not just a file. MythOS's Map (`mythos-os/src/types/index.ts`) already answers
this for one domain — `plane` ("tangible"/"abstract"), `horizon`+derived `reach` (which
produces Village/Kingdom/Empire), and an 8-value `EdgeKind` (`requires`/`enables`/`blocks`/
`funds`/`schedules`/`routes_through`/`costs`/`restores`) that reads as domain-agnostic as
written. Nova's framing: code, a business, a film shoot, and a supply chain all run on the
same node/edge primitive — code is just the domain transparent enough to parse automatically.

**Chosen:** converge on one shared spec, not one shared codebase. Full spec:
`~/Documents/Vault/40-reference/node-graph-model.md`. Two additions to what MythOS has today,
both written there in full: `kind` becomes an open vocabulary (MythOS's `NodeKind` is a closed
16-value union; S-IDE's own `FileNode.category`/`tags` are already open strings, and that's the
shape needed) rather than a fixed enum, organized into domain "kind packs"; and edge provenance
becomes three-way (`authored` / `parsed` / `inferred`) rather than MythOS's binary
`approved`/`confidence`, because a parser-found import is a fact, not a model's guess, and
treating it like one would put every code edge through a review queue that makes no sense for
it.

**Why:** MythOS solved the hard parts (the two-plane split, horizon/reach-derived scope,
directed typed edges with provenance) once, for a life. Building S-IDE a second, parallel
model would be redundant work that drifts from MythOS's the moment either one changes.

**Explicitly not decided here:** no shared library, no code changes in either repo. S-IDE's
`graph/types.py` doesn't compute `plane`/`horizon`/`status` today — extending it toward this
spec is scoped work for a future round, not done by this entry. The `SideNode` bridge
(`/api/nodes`, `/api/infra`) stays exactly as frozen below; this spec is the target for a
*future* versioned bridge evolution, not a change to the live contract. MythOS's own
`NodeKind` migration is that repo's call, on its own round protocol — not made here.

**Revisit when:** either repo actually starts building against this (at which point the open
questions in `node-graph-model.md` — shared `reach`/scope derivation, MythOS's own migration
timing — need answers) or the convergence call turns out to be wrong in practice.

## 2026-08-06 — the UX vision is a rebuild; the bridge contract is frozen through it

**Context:** Nova's target for the frontend is closer to Obsidian's Canvas merged with
Scratch — one DOM-backed surface spanning zoom levels, live/composable nodes — than an
incremental improvement on `gui/app.html`'s current canvas-plus-four-tabs layout. Full
reasoning and the component-by-component inventory: `~/Documents/Vault/20-projects/s-ide/
the-canvas.md`. Scope (fresh UI only vs. a data-model extension too) is still open; not
decided here.

**Chosen (the one piece that is decided):** `GET /api/nodes` and `GET /api/infra` keep their
exact `SideNode` shape, unchanged, for the duration of any UX rewrite — same additive-only
rule as the 2026-08-05 bridge-stability decision below, just reaffirmed explicitly now that a
rewrite is actually on the table. MythOS's `SideImporter`/`InfraView` were verified against
their real fetch calls, not assumed to still work.

**Why:** A frontend rewrite is exactly the kind of change that tempts a contract break "since
we're already in there." The bridge is a cross-repo dependency; breaking it is MythOS's
problem too, and gets its own coordinated round if it ever needs to happen — not a side effect
of a UI decision made in this repo alone.

**Not decided here, tracked as open in `the-canvas.md`:** whether the rewrite covers the
frontend only (parser, graph model, and bridge untouched) or also extends `graph/types.py`
with live/runtime and authored-vs-inferred-edge concepts the current static model has no field
for. Round 5 does not get written until Nova picks.

## 2026-08-08 — `/api/infra` passes typed edges through as an additive per-node field; graphs are named and selectable

**Context:** The orchestration-visibility work (`~/Documents/Vault/40-reference/
orchestration-visibility.md`) requires that hand-authored graphs' typed edges (`dispatches`,
`reads/writes`, `blocks`, …) be visible to consumers, not just the node hierarchy. `_get_infra`
previously emitted only `nodes` + `childIds` — the `edges` array in `web-infra.json` was never
serialized, so edge *types* were lost the moment a graph left the file. This is the S-IDE side
of a node-graph-model concern: edges are the semantics (node-graph-model.md), not decoration.

**Chosen:** three changes, all within the frozen bridge contract:

1. **Typed edges pass through per-node, not top-level.** The response stays a bare array —
   MythOS's `InfraView` (`mythos-os/src/components/InfraView.tsx:70`) does
   `const data: InfraNode[] = await res.json()`, so any top-level field is a contract break.
   Instead each node gains an additive `edges: [{from, to, type}]` field listing its
   **outgoing** edges, ids `infra:`-prefixed to match the node id namespace. The union across
   nodes is the full edge set, so nothing is lost and no relationship is double-counted.
   Edges whose `from` is not a node are dropped (nothing to attach them to).
2. **`?graph=` query param selects a named graph file.** `web-infra` → `web-infra.json`
   (default, unchanged), `relay` → `relay.graph.json`, `plans` → `plans.graph.json` — all
   committed files at the repo root, read on each request exactly as `web-infra.json` always
   was. An unknown graph name is a structured 404 (`{"error": "unknown graph: <name>"}`) —
   a malformed request, not an empty import, matching the bridge's other error cases.
3. **`relay.graph.json` is registered.** The hand-authored relay orchestration graph
   (`opus-01` → subsystems → daily jobs/gates, the artifact of the orchestration-visibility
   work) becomes a first-class named graph served by the same endpoint.

**Why:** The additive-only stability rule (2026-08-05) permits new fields freely; a per-node
`edges` field is exactly that — MythOS's `InfraNode`/`SideNode` readers ignore unknown keys
(verified in 2026-08-05 round-2 audit: `calendarBridge.ts` reads only the documented fields).
Edge types belong on the wire because they are the graph's meaning; a consumer (MythOS,
InfraView, or S-IDE's own future canvas) that only sees `childIds` gets hierarchy without
flow, which is the exact gap this work closes.

**Explicitly not decided here:** no S-IDE frontend rendering yet (the new `/api/infra?graph=`
paths and the `edges` field are additive data; the canvas work is the separate UX-rebuild
decision above, still open). `graph/types.py`'s `Edge` model is untouched — `edges` are
passed through structurally, not validated against `Edge`. MythOS coordination is not needed
for additive fields, but this entry is the record in case `InfraView` wants to render edges.

**Revisit when:** MythOS's `InfraView` (or S-IDE's canvas) starts consuming `node.edges` and
wants a shared edge vocabulary; or if a graph name collides with a future generated-graph
plan.

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

## 2026-08-05 — round 4: `ai/teams.py` is deliberately dead; git history is the archive

**Context:** Round 1 flagged `ai/teams.py` (460 lines — `TeamSession`, turn-based multi-agent
orchestration with role-scoped tool permissions, sandboxed per-agent project copies, and a
human-approval gate) as deleted *consequentially* rather than *specifically* deliberately. Its
fate was carried through rounds 1–3 as the one open question. Round 4 was asked to decide for
real, options: (1) accept as deliberately dead, (2) preserve the source somewhere documented,
(3) port the orchestration into the current tree as an OpenCode-driving CLI.

**Chosen: option 1 — deliberately dead, with the preservation pointer folded in.** The source
is preserved by git history at `e772b8b^` (`git show e772b8b^:ai/teams.py`), so option 2's
only value-add — "don't lose it" — is already satisfied by the VCS; nothing is lost to time.

**Why not port (option 3):** `ai/teams.py` is not a self-contained engine. It imports
`ai.client.OllamaClient`, `ai.context`, `ai.roles`, `ai.tools`, and `build.sandbox` — five
more modules that went out with the same rewrite. The multi-agent engine is inseparable from
the in-app Ollama stack it orchestrated; there is no standalone "teams" unit to rescue.
Porting it to drive OpenCode sessions would be a new feature (what would multiple agent
sessions do that one OpenCode instance doesn't already do in the round protocol?), with no
signal from the vault or the codebase that anyone wants it. It stays a proposed idea, not a
planned round.

**Why not preserve-and-archive (option 2 as a separate step):** redundant with git. A copied
`ai/teams.py` in a `legacy/` directory would be dead, untested, unimportable code in the tree —
worse than git history, which keeps it frozen next to the exact `ai.client`/`roles`/`tools`
APIs it called. The recovery pointer lives here instead.

**The role `ai/` played is replaced, not vacant:** the round protocol (`AGENTS.md`) +
`opencode.json` permission scoping + the human-driven round gate do what teams.py did — an
agent with scoped tools whose output is reviewed before it lands — at repo level, with OpenCode
as the single agent. `ai/tool_builder.py` and `ai/roles/` (self-improving tool creation, six
role definitions) are covered by the same verdict: their functionality was in-app Ollama
surface that OpenCode supersedes.

**Reversible by Nova:** the decision is one line in this file away from being overturned; the
source is one command away (`git show e772b8b^:ai/teams.py`). `ai/` itself stays deleted either
way — restoring it would need stop-condition 4 handling.

## 2026-08-05 — round 4: `/api/metrics` is dead surface and was removed

**Context:** Round 1 noted `/api/metrics` "reads data nothing produces" — `.side-metrics.json`
was written by the deleted `monitor/profiler.py`; the only surviving writer, `/api/xp`, appends
`xp_log`/`total_xp`, not the `files`/`functions`-with-`avg_ms` shape `_get_metrics` read. Round 4
verified: no caller in `gui/app.html`, no producer, no external consumer (greps of mythos-os and
n3xu5 clean), no test.

**Chosen:** remove the route, its dispatch entry, the handler, and the route-table comment. The
frontend's Profile button that once called it was already removed in round 1; this is the
server-side half of the same reconciliation. The `SideNode` bridge (`/api/nodes`, `/api/infra`)
is untouched — `/api/metrics` was never part of the cross-repo contract. No `_record_xp`/`/api/xp`
behavior changed; MythOS quest XP recording is unaffected.

## 2026-08-05 — round 4: `web-infra.json` has drifted; generator decision proposed for Nova, file left untouched

**Context:** `web-infra.json`'s `meta.generatedAt` is `2026-07-19`. Re-validated against n3xu5's
live infra (wrangler configs + schemas under `~/DevOps/WebDev/n3xu5`) on 2026-08-05. Everything
the graph tracks is still accurate — workers `homepage`/`n3xu5-auth`/`n3xu5-email-gate`,
D1 `n3xu5-auth`, R2 `n3xu5-mail`, `RL_AUTH`/`RL_MSG`, `DAILY_SALT`/`RESEND_API_KEY`, all three
domains, SSO/email-routing/calendar-api, resend/porkbun/c2-panel. **But it has drifted — five
things n3xu5 added since the graph's date are missing:**

| Missing from web-infra.json | Reality |
|---|---|
| `n3xu5-home` worker | apex `n3xu5.art` landing page, deployed 2026-07-27 (8 days after `generatedAt`) |
| `n3xu5-pages` worker | wildcard `*.n3xu5.art` per-user pages worker |
| R2 `n3xu5-pages` bucket | page content (auth worker writes, pages worker reads) |
| R2 `n3xu5-files` bucket | Round 5.3 encrypted user-file storage |
| `ChatRoom` durable object | chat DMs + groups, `/chat` routes on both domains |

**Chosen: flag to Nova, do not regenerate.** The round-2 decision (2.3) keeps `web-infra.json`
hand-maintained with a structural-integrity test; the generator option was proposed-but-not-built
"because it crosses repo boundaries and regenerating real infrastructure data on a schedule is a
decision Nova should make." That now fires: the file is demonstrably drifting (the 2026-07-27
deploy and the R2/FILES/chat additions are exactly the class of change a generator or a hand-
update should catch). Proposed to Nova: either (a) hand-update the five missing nodes/edges now
and keep the hand-maintenance model with a stated review cadence, or (b) build the generator
(option 2 from round 2) reading n3xu5's wrangler configs + schema. Not done unilaterally this
round — a silent regen of committed infra data is exactly what the brief forbids.

## 2026-08-05 — round 4: local imports do NOT count as workspace deps; sibling-project resolution is a separate proposed feature

**Context:** Round 3 documented the fast/slow divergence in `_collect_external_imports` (fast =
graph `isExternal` edges only, so third-party + stdlib; scan = all `.py` imports, which
mis-guesses this repo's own packages `graph`/`parser`/`process` as external). The open question
carried to round 4: do local imports count as workspace deps?

**Chosen: no — keep both paths as-is; sibling-project dependency resolution is a distinct,
unimplemented feature.** The graph's external edges encode exactly "not local"; folding local
imports into `_collect_external_imports` would contaminate the fast path with the repo's own
packages (the round-3 scan output shows that pollution). The workspace feature's headline —
"which sibling projects does this project import?" — is real but is not the job of an
*external*-import collector: it needs `resolve_project_deps` to resolve local imports against
`manifest.projects` (directories), not fold them into `manifest.packages` matching. That is a
feature build, not a consolidation fix. **Flagged as a proposed round-5+ item** (workspace
sibling-dep resolution), same status as the web-infra generator.
