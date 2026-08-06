# Round 4 — consolidation: dead code, docs, and `ai/teams.py`'s fate

Read `AGENTS.md` and `ROADMAP.md` first if you haven't. This is the consolidation round —
nothing new gets built, and anything that crosses the "delete something not already staged"
line (stop condition 4) gets written into `ARCHITECTURE-DECISIONS.md` **before** it happens,
not after. Round 1's audit flagged `ai/teams.py`'s deletion as consequential-not-deliberate
(`git show HEAD:ai/teams.py` recovers it); round 3 added three smaller items. This round
decides each one for real.

## 4.0 — Decide `ai/teams.py`'s fate (the round-1 open question)

`ai/teams.py` was 460 lines of real turn-based multi-agent orchestration (`TeamSession`,
role-scoped tool permissions, sandboxed per-agent project copies, a human-approval gate). It
was deleted with the rest of `ai/` and nothing in the rewrite mentions it. The round protocol's
agent is a single OpenCode instance — but "teams" is a distinct capability from "one agent
drives the repo."

Options, in increasing commitment:

1. **Accept it as deliberately dead.** The deletion goes with the rest of `ai/`; the round
   protocol supersedes it. Write the decision down and move on. (Cheapest. This is the
   default unless a reason to port exists.)
2. **Preserve the source** without reviving it — recover `ai/teams.py` into a documented
   archive location (or an `ARCHITECTURE-DECISIONS.md` note pointing at the exact commit to
   restore from), so it isn't lost to history. Note: it is already recoverable from git
   (`git show HEAD:ai/teams.py`), so this may add nothing beyond a pointer.
3. **Port the orchestration** into the current tree (e.g. as a CLI subcommand that drives
   multiple OpenCode/agent sessions). This is a feature build, not consolidation — it needs a
   real round brief of its own and should be *proposed*, not built, this round.

**Acceptance:** `ARCHITECTURE-DECISIONS.md` states which option, with the reasoning; if
option 3, a proposal (not an implementation) exists for the round that would build it. `ai/`
itself stays deleted either way — reviving it would need stop-condition 4 handling.

## 4.1 — Dead-code sweep

Round 1's inventory and rounds 2–3's audits have mentioned several live-code debt items.
Verify each and fix or consciously defer (with a reason):

- **Python 3.13 `DeprecationWarning`s** surfaced in every `self-check` run:
  `ast.Constant.s` at `parser/parsers/python_parser.py:248` (deprecated in favor of
  `.value` — will break in 3.14) and the multiprocessing fork warning from
  `ProcessPoolExecutor` (multi-threaded fork). The `ast.Constant.s` one is a real forward-compat
  fix; the fork one may need `mp_context="spawn"` or is noise worth suppressing.
- **`/api/metrics` in `gui/server.py`** — round 1 noted nothing in-tree generates
  `.side-metrics.json` except `/api/xp`. Is `/api/metrics` still serving real data or is it
  dead surface? Verify against the frontend (`gui/app.html`) before touching.
- **`web-infra.json` drift** — its `meta.generatedAt` is `2026-07-19` (round 2 accepted
  hand-maintenance). Re-validate it still matches n3xu5's actual infra (see
  `~/DevOps/WebDev/n3xu5/wrangler.toml`, D1 schema) — if it's drifted, that's a proposed
  generator decision for Nova, not a silent regen.
- **`parser/workspace.py` fast/slow divergence** (round 3): whether local imports count as
  workspace deps, and whether the fast path should also consider sibling-project imports.
  This is a behavior decision — write it down either way.
- **Anything round 1's `grep` sweep missed.** Re-run the dangling-reference greps from
  `tasks/round-1/00-ROUND-1.md` §1.5 against the now-committed tree.

**Acceptance:** each item ends in a fix, a decision-log entry saying it stays and why, or a
flag to Nova. `self-check` still exits 0.

## 4.2 — Reconcile `README.md` against what actually landed

Round 1 committed `README.md` as already rewritten for the post-rewrite architecture. Two
things since changed it: round 2 added the "Stability rule" and error-contract paragraph to
the MythOS bridge section, and round 3 touched workspace/parser internals (no README change).
Check:

- The architecture diagram matches the real tree (no `monitor/`/`ai/`/`version/` references
  in it; `gui/app.html`, `parser/`, `graph/`, `process/`, `main.py`, `run.py` all exist).
- The **MythOS bridge API** example `SideNode` still matches `_get_nodes` output exactly
  (field-for-field, including `_source`).
- Quick-start commands (`python run.py`, `python main.py parse`) still work as documented.
- `CHANGELOG.md` reflects round 2 and 3 changes (bridge tests/error contract, workspace fix).

**Acceptance:** README and tree agree; any discrepancy fixed or logged.

## 4.3 — Mirror the round outcome to the vault

Round outcomes go back to `~/Documents/Vault/20-projects/s-ide.md` (the canonical project
note, per `AGENTS.md`). Update it with: round 3's findings (workspace fast-path fix, coverage
floor verification), round 4's decisions (especially `ai/teams.py`), and the round counter.
The vault lives outside this repo — touching it is normal here (n3xu5/mythos-os do the same).

**Acceptance:** the vault note reflects the round-3 close and round-4 close. If the vault path
doesn't exist or can't be written, say so in the audit and flag it — don't silently skip.

## 4.4 — Close

1. `python main.py self-check .` — must exit 0.
2. `tasks/round-4/AUDIT-R4.md` with real output.
3. Any decision-not-followed goes into `ARCHITECTURE-DECISIONS.md`.
4. Update `ROADMAP.md`'s status table (round 4 → done; the round protocol then parks until
   Nova says continue).
5. One item, one commit. Report to Nova: what got fixed, what got decided, what got deferred —
   and specifically the `ai/teams.py` verdict.
6. Stop. **Do not start round 5** — there is no round-5 brief until Nova writes one.

## Carried into round 4 from earlier rounds

- `ai/teams.py` fate (round 1 flag; `ROADMAP.md` round-4 slot).
- `ast.Constant.s` + multiprocessing `DeprecationWarning`s (round 3 audit §3.3).
- Fast/slow `_collect_external_imports` divergence — do local imports count as workspace deps?
  (round 3 decision log).
- Dead `Profile`/`Archive` UI removal was handled in round 1; verify nothing similar remains.
- `web-infra.json` hand-maintenance re-validation (round 2 decision log; revisit clause).
