# Round 2 — harden the MythOS/n3xu5 bridge (`/api/nodes`, `/api/infra`)

Read `AGENTS.md` and `ROADMAP.md` first if you haven't. This round exists because two other
repos (MythOS's `SideImporter`/`InfraView`) depend on the `SideNode` shape that `gui/server.py`
exports, and round 1 confirmed the bridge has **no real test coverage** — the existing
`TestSideNodeAdapter` re-implements the adapter logic inline rather than calling the handlers,
and it has already drifted from the real code. This round makes the contract testable and the
error behavior explicit. See `ARCHITECTURE-DECISIONS.md` 2026-08-05 entries and
`tasks/round-1/AUDIT-R1.md` §1.6 for the baseline.

## 2.0 — Replace the simulation with real handler tests

`test/test_suite.py`'s `TestSideNodeAdapter` (around line 1015) currently *simulates* the
`_get_nodes` / `_record_xp` logic instead of calling `gui/server.py`'s handlers. It has
already drifted from the real server in two places (found in round 1's audit):

- `LANG_SKILL_HINTS` is keyed by extension (`.py` → `"python"`) in the test, but
  `gui/server.py:351` keys by `ext.lstrip(".")` → `"python"`. Equivalent for `.py`, but the
  test's mapping is narrower than the server's (the server also covers `.html`, `.css`, `.go`,
  `.rs`, `.sh`, `.md`, `.ts`).
- The test builds `id` as `f"side:{n.get('id', '')}"`; the server uses
  `n.get('id', n.get('path', ''))` — for nodes without an `id`, the fallback differs.

And `_get_infra` has **zero** coverage of any kind.

Rewrite these tests to exercise the real code:

1. **Instantiate a real `Handler`** (it's a `BaseHTTPRequestHandler` subclass; construct it
   with a fake socket/request or, more simply, refactor `_get_nodes(qs)`/`_get_infra()` to be
   pure-ish methods and call them on a `Handler` instance with a stub `_json`/`_error` that
   capture the response — `_json` and `_error` are the only HTTP-coupled parts).
   Alternatively call them through an in-process `HTTPServer` on a loopback port, but prefer
   the direct-handler approach first: no ports, no threads, hermetic.
2. **Fixture:** a synthetic project (temp dir) parsed with `parse_project(save_json=False)` or
   a hand-built graph dict written to `.nodegraph.json`, so the tests don't depend on the
   s-ide tree's own current graph. The existing tests read `_PROJECT_ROOT`'s live
   `.nodegraph.json` — that couples the suite to the repo's own state; drop it.
3. **Assert the `SideNode` shape exactly** per `README.md`'s "MythOS bridge API" section:
   `id`, `label`, `detail`, `kind`, `category`, `skillHints`, `estimateHours`, `childIds`,
   `_source`. Include the `childIds` filtering (only node names present in the graph) and the
   `_source` metadata block, which today's tests don't cover at all.
4. **Cover `_get_infra`** against a temp `web-infra.json` (write a small fixture with nodes +
   edges; assert `infra:`-prefixed ids, `tech` → `skillHints`, `childIds` mapping). Note
   `_get_infra` resolves `web-infra.json` relative to `_ROOT_DIR` (`gui/server.py:468`) — a
   temp-file fixture means it must point there or the path must be parameterized; decide which
   (see 2.2).
5. **Cover `_record_xp`** for real: call the handler and assert `.side-metrics.json` contents
   (the current test re-implements the append logic and only checks its own result).

**Acceptance:** every test in `TestSideNodeAdapter` calls a real `gui.server` handler (no
inline re-implementation of `_get_nodes`/`_get_infra`/`_record_xp`); `python test/test_suite.py
-q` exits 0; paste the output.

## 2.1 — Error-handling contract

Decide, test, and document what the bridge returns when things are not happy:

- `/api/nodes` with **no `root`** → currently `{"error": "root required"}` (HTTP 400).
- `/api/nodes` with a **root that isn't registered / has no `.nodegraph.json`** →
  currently `[]` (`gui/server.py:369`). Is an empty array the right contract for a bridge
  consumer, or should it be a distinct shape/status so MythOS can tell "project has no graph"
  from "project has an empty graph"?
- `/api/nodes` with a **root that fails to parse** → the handler only reads the cached graph,
  so a missing graph is the realistic case; a corrupt `.nodegraph.json` currently makes
  `_load_graph` return `None` → `[]`. Decide whether corrupt-graph should surface.
- `/api/infra` with **`web-infra.json` missing** → currently `[]` (`gui/server.py:470`).
- `/api/infra` with **`web-infra.json` corrupt** → currently a structured 500
  `{"error": "Failed to load web-infra.json: ..."}` (`gui/server.py:474`). A bridge consumer
  may treat any non-2xx as "infra down"; decide whether that's the contract you want, or
  whether a 200-with-error-shape is safer for `InfraView`.

**Acceptance:** each case has a test asserting the actual HTTP status + body, and the decisions
are written into `ARCHITECTURE-DECISIONS.md` with the rationale (this is a contract change —
see stop conditions).

## 2.2 — A stability rule for the `SideNode` shape

Write down (in `README.md` and `ARCHITECTURE-DECISIONS.md`) a stated rule for changing the
`SideNode` shape. Pick one and say why:

- **Additive-only:** new fields allowed, no renames/removals/type changes without a
  `ARCHITECTURE-DECISIONS.md` entry + coordinated bump in MythOS. (This matches the current
  stop-condition posture in `AGENTS.md`.)
- Or **versioned** (e.g. `X-SideNode-Version` header / `version` field in the payload).

Do not change any existing field's meaning in this round unless a 2.1 decision requires it.

## 2.3 — `web-infra.json` provenance (revisit; may stay as-is)

`web-infra.json`'s own `meta.generatedAt` is `2026-07-19`, and nothing regenerates it — it is
hand-maintained and can drift from n3xu5's actual `wrangler.toml` routes, D1 schema, etc.
Options, in increasing effort:

1. **Keep hand-maintained**, but add a test that every node referenced in `edges` exists and
   every `childIds` target exists (structural validation). Cheap, catches typos.
2. **Add a generator** that reads n3xu5's `wrangler.toml`/schema and regenerates it. This
   crosses repo boundaries and is a real decision — propose it in `ARCHITECTURE-DECISIONS.md`
   rather than building it unilaterally.

**Acceptance (option 1):** a test validates `web-infra.json`'s edge/childId integrity; the
decision (1 vs 2, and why) is in `ARCHITECTURE-DECISIONS.md`.

## 2.4 — Close

1. `python main.py self-check .` — must exit 0.
2. `tasks/round-2/AUDIT-R2.md` with real output.
3. Any decision-not-followed goes into `ARCHITECTURE-DECISIONS.md`.
4. Write `tasks/round-3/00-ROUND-3.md` expanding `ROADMAP.md`'s sketch (parser coverage +
   workspace features) in light of what round 2 found — e.g. if `_get_nodes` now needs the
   `side:` id to match `nodegraph.json`'s `id` field consistently, that may surface a parser
   or adapter gap worth a round-3 item.
5. Update `ROADMAP.md`'s status table.
6. One item, one commit. Report to Nova: what passed, what's still red and why, and any
   `SideNode` contract decision that needs a coordinated MythOS change.
7. Stop.

## Open questions from round 1 that touch this round

- `_get_nodes` derives `id` as `f"side:{n.get('id', n.get('path', ''))}"`. The `side:`-prefix
  contract is documented in `README.md`; the `_source.path` field preserves provenance.
  Round 2 should confirm MythOS only depends on the documented fields before tightening the
  id derivation.
