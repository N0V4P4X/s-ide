# Round 3 — parser coverage + workspace features that atrophied during the rewrite

Read `AGENTS.md` and `ROADMAP.md` first if you haven't. This round exists because the old
test suite (per `CHANGELOG.md` 0.5.1) had ~301 tests across 45 classes; the current tree has
121 across 20. Some of that drop is the legitimate removal of `ai`/`build`/`monitor`/`version`
tests (round 1 verified that), but round 1 flagged that **parser/graph/workspace coverage
itself** may have regressed, and round 2 added a new concern: the MythOS bridge's output
quality is directly downstream of the parser's graph node fields. Round 3 verifies the
coverage floor and restores the workspace features that lost their tests.

Round 2's open question resolved cleanly: the `side:` id derivation and `childIds` filtering
were confirmed consistent with what MythOS reads (`sideNodeId` is opaque to it), so there is
**no** pending adapter-side debt carried into this round. The bridge's `childIds` matching —
basename of `imports[].source` vs. basename of `nodes[].path` — is worth a parser↔bridge
integration test (3.2) precisely because it was never exercised against real parser output.

## 3.0 — Verify the parser-coverage floor

Current coverage (per `test/test_suite.py`): `TestPythonParser`, `TestJSParser`,
`TestJSONParser`, `TestShellParser`, `TestWalker`, `TestProjectConfig`, `TestResolveEdges`,
`TestLayout`, `TestDocCheck`, `TestProjectParser`, `TestTomlParser`, `TestYamlParser`,
`TestPythonParserDataFlow`, `TestFilterLogic`, `TestDocLinks`, `TestCalculatorExample`.

Before adding anything, check what the rewrite *dropped* that wasn't subsystem-specific:
`git log --diff-filter=D` on `test/` and diff the old test module against the current one to
enumerate removed test names. For every removed test that covered `parser/`, `graph/`, or
`parser/workspace.py` (not `ai`/`build`/`monitor`/`version`), decide: still-relevant →
re-add or fold into an existing class; obsolete → say why it's obsolete. Specifically look
for:

- Python data-flow depth: `calls`, `raises`, `complexity`, `args`/`return_type` on
  `Definition` — the current `TestPythonParserDataFlow` should be checked against the
  full field set.
- `resolve_edges` external-package handling (`collect_external_packages`, `external_pkg`
  edges).
- Walker ignore rules (`_should_ignore`) — `.gitignore` / `.side-*` / `node_modules` /
  venv handling.
- TOML/YAML beyond the 7 existing tests (nested deps, pyproject tool sections — round 1
  found the "3 failing tests" report was stale; the parsers are fine but thin).

**Acceptance:** a table in `AUDIT-R3.md` lists each removed parser/graph/workspace test with
its disposition (restored / folded / obsolete+why). Coverage for the items above is real and
green; `python3 test/test_suite.py -q` exits 0.

## 3.1 — Restore the atrophied workspace features

`parser/workspace.py` (shared devspace dependency manifests) has `find_workspace_root`,
`find_projects_in_workspace`, `resolve_project_deps`, `_collect_external_imports`, and
`add_package` — but the current `TestWorkspaceManifest` only exercises
`init`/`save`/`summary`/`add`/`remove`/`requirements_txt`. The discovery and cross-project
dependency-resolution functions have **zero coverage**. Verify they still work against the
current graph shape (round 2 confirmed the bridge consumes `.nodegraph.json`; workspace
resolution likely reads the same nodes), then:

1. `find_workspace_root` / `find_projects_in_workspace` — write a temp workspace fixture
   (workspace marker + a couple of project dirs) and assert discovery. Fix drift if the
   rewrite changed the marker or the discovery rules.
2. `resolve_project_deps` — assert it maps a project's `imports[].source` values to sibling
   projects in the workspace (that's the feature's point). If it depends on a shape the
   rewrite changed, fix the shape usage here — the parser and the bridge both depend on the
   same nodes, so this is the natural place to catch drift.
3. `add_package` — the manifest method exists; `add_package` at module level (line ~312)
   may be a different thing; make sure whichever is public is tested.

**Acceptance:** every public function in `parser/workspace.py` is called by at least one
test; the tests use temp-dir fixtures (no coupling to this repo's own workspace state);
`python3 test/test_suite.py -q` exits 0.

## 3.2 — Parser → bridge integration (new, from round 2)

Round 2's `TestSideNodeAdapter` uses hand-built graph dicts — hermetic and correct, but it
means the bridge's contract is only tested against *idealized* parser output. The
`childIds` basename-matching and `skillHints` import-derivation have never been exercised on
**real** `parse_project` output. Add one integration test:

- Parse a synthetic project with `parse_project(...)` (a temp dir with a `.py` that imports
  a sibling module and a stdlib module, plus a `README.md`), `save_json=False` or write the
  result to the temp dir's `.nodegraph.json`.
- Feed that real file through `Handler._get_nodes` (same stub `_json`/`_error` pattern as
  round 2).
- Assert the imported sibling appears in `childIds` (it's in the graph), the stdlib import
  does **not** (it's not), and the `.py` node's `skillHints` start with `["python", "backend"]`.

This is the first test that locks the parser's `imports`/`path` conventions to the bridge's
assumptions — the two halves of the pipeline that MythOS ultimately depends on.

**Acceptance:** the integration test passes on real parser output; if it exposes drift
(parser writes imports differently than the bridge expects), fix the *parser* or the
*bridge* with an `ARCHITECTURE-DECISIONS.md` note and say which — the bridge's `SideNode`
shape is frozen additive-only, so any mismatch is more likely a parser-side fix.

## 3.3 — Close

1. `python main.py self-check .` — must exit 0.
2. `tasks/round-3/AUDIT-R3.md` with real output, including the 3.0 disposition table.
3. Any decision-not-followed goes into `ARCHITECTURE-DECISIONS.md`.
4. Write `tasks/round-4/00-ROUND-4.md` expanding `ROADMAP.md`'s consolidation sketch in
   light of what round 3 found (dead-code sweep, `README.md` architecture diagram vs
   reality, `ai/teams.py` fate decision, vault mirror).
5. Update `ROADMAP.md`'s status table.
6. One item, one commit. Report to Nova: what passed, what's still red and why, and any
   parser-shape change that touched the bridge.
7. Stop.

## Open questions from round 2 that touch this round

- The bridge's `childIds` filter uses `imports[].source` basename (last `.`-dotted
  component) matched against `nodes[].path` basename-without-ext. For imports written as
  `package.module`, only the last component is matched — a `from package.module import x`
  whose node is `package/module.py` won't match (basename `module` vs path component
  `module.py` → actually does: `module.py` → `module`). But dotted-path imports whose file
  lives in a package directory (`package/module.py` imported as `package.module`) rely on
  the path basename coinciding. Confirm whether that's a real gap worth a round-4 item.
