# S.I.D.E. — roadmap

Rounds, in order. Each is a "continue" from Nova. `AGENTS.md` has the protocol; the short
version is **work → self-audit → write the next round → stop.**

Round briefs here are deliberately short. **The agent expands each round into
`tasks/round-N/00-ROUND-N.md` at the end of the previous round**, adapted to what it actually
found. A brief that no longer makes sense should be rewritten, not followed — say why in
`ARCHITECTURE-DECISIONS.md`.

## status

| round | what | state |
|---|---|---|
| **1** | reconcile the uncommitted v0.6.0 rewrite; land it; adopt the round protocol | **done** — landed, `self-check` green, 7 commits; audit at `tasks/round-1/AUDIT-R1.md`. `ai/teams.py` fate flagged for Nova (round 4 slot) |
| 2 | harden the MythOS/n3xu5 bridge (`/api/nodes`, `/api/infra`) | **done** — real-handler tests, error contract + stability rule decided, `web-infra` validated; audit at `tasks/round-2/AUDIT-R2.md` |
| 3 | parser coverage + workspace features that atrophied during the rewrite | **next** — brief at `tasks/round-3/00-ROUND-3.md` |
| 4 | consolidation — docs, dead-code sweep, decide the fate of `ai/teams.py` | planned |

---

## Round 1 — reconcile and land the rewrite

The repo has been mid-rewrite for a while: `README.md` already describes a leaner
Python-server-plus-browser-canvas architecture, but the deletion of the old Tkinter/AI-Teams
stack (`ai/`, `build/`, `monitor/`, `version/`, plus `migrate.py`, `update.py`, three
top-level docs) sits **uncommitted** in the working tree, alongside real edits to `main.py`,
`gui/server.py`, `gui/app.html`, `parser/project_parser.py`, `run.py`, and
`test/test_suite.py`. Nothing about this is hidden — `git status` shows all of it — but nobody
has closed the loop: tests are red, CI is broken as written, and the changelog doesn't mention
any of it.

Full brief: `tasks/round-1/00-ROUND-1.md`. Headline items:

- Inventory the diff and report before deciding anything — same posture n3xu5/mythos-os round
  1s take with inherited uncommitted work: it's real, it's mostly intentional, but "mostly"
  isn't "entirely."
- Triage and fix (or knowingly defer, with a reason) the 3 failing `TestTomlParser` tests.
- Fix the CI gap: `ci.yml` calls `self-check . --json`, which doesn't exist.
- Write the missing `CHANGELOG.md` 0.6.0 entry.
- Git hygiene: untrack the files matching `.gitignore` that are still tracked
  (`.nodegraph.json`, `.side-metrics.json`, `versions/*.tar.gz`); decide `projects.json`
  (gitignore — it hardcodes a local path) and `web-infra.json` (commit — it's real content the
  `/api/infra` bridge serves) deliberately, not by default.
- Confirm nothing still imports the deleted `ai`/`build`/`monitor`/`version` modules.
- Bring in `AGENTS.md` / `ROADMAP.md` / `ARCHITECTURE-DECISIONS.md` / `opencode.json` (this
  round scaffolds them; round 1 is the first round to actually run against them).

**Ends with a decision flagged for Nova, not made unilaterally:** whether `ai/teams.py`'s
multi-agent orchestration is deliberately dead now that OpenCode drives the repo directly, or
should be preserved/ported. See `AGENTS.md` stop condition 4.

## Round 2 — harden the bridge (sketch, expand after round 1)

`/api/nodes` and `/api/infra` are consumed by another repo (`~/DevOps/WebDev/mythos-os`) that
this repo's tests can't see. That asymmetry is the risk.

- Test coverage for both endpoints — currently neither appears to have dedicated tests; verify
  and close the gap.
- Error handling: what does `/api/nodes` return for an unregistered `root`, a project that
  fails to parse, or `web-infra.json` missing? Right now `_get_infra` in `gui/server.py`
  already returns a structured 500 when the file is missing — decide whether that's the right
  contract for a bridge consumer to depend on, or whether it should be a distinct status/shape.
- A stated stability rule for the `SideNode` shape — versioned, or just "additive only, breaking
  changes get flagged in `ARCHITECTURE-DECISIONS.md` and coordinated with Nova" — written down
  instead of implied.
- Revisit whether `web-infra.json` should be generated (from n3xu5's actual `wrangler.toml`
  routes, D1 schema, etc.) instead of hand-maintained — it's dated `2026-07-19` in its own
  `meta.generatedAt` and nothing currently keeps it in sync with n3xu5's real infrastructure.

## Round 3 — parser coverage + workspace features (sketch)

The old test suite had ~301 tests across 45 classes (per `CHANGELOG.md` 0.5.1); the current
tree has 112. Some of that drop is the legitimate removal of `ai`/`build`/`monitor`/`version`
tests, but round 1 should confirm the parser/graph/workspace test coverage itself didn't
regress in the process. `parser/workspace.py` (shared devspace dependency manifests) and the
TOML/YAML parsers are the likely places to check first, since round 1 already finds 3 failures
there.

## Round 4 — consolidation (sketch)

- Dead code sweep once round 1's reconciliation is committed.
- Reconcile `README.md`'s architecture diagram against whatever actually landed.
- Decide `ai/teams.py`'s fate for real, if round 1 only flagged it.
- Mirror the outcome back to `~/Documents/Vault/20-projects/s-ide.md`, same as mythos-os keeps
  its vault note in sync with round outcomes.

---

## Principles that don't change

- The bridge API (`/api/nodes`, `/api/infra`) is a contract with repos outside this one.
  Breaking it silently breaks MythOS.
- Stdlib Python, framework-free JS. A new dependency is a stop condition, not a `pip install`.
- **"Implemented" is not a result.** Every round shows command output, not a description of
  what should have happened.
- `self-check` is the gate. If it's insufficient, extend it — don't work around it.
