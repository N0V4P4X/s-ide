# Round 3 audit — parser coverage + workspace features

Ran 2026-08-05. Gate: `python main.py self-check .` exits 0. Every item below shows the
actual command and its output — not a description of what should have happened.

## 3.0 — Verify the parser-coverage floor

Baseline: pre-rewrite suite had 305 tests across 45 classes (`git show e772b8b^:test/test_suite.py`).
The rewrite's reconcile commit cut it to 112; round 2 brought it to 121. The 3.0 question is
whether parser/graph/workspace coverage itself regressed, not just the ai/build/monitor/version
tests. Enumerated the removed tests by diffing class+method names between the two modules:

```
$ git show e772b8b^:test/test_suite.py > /tmp/opencode/old_tests.py
$ python3 - <<'PY'
import re
def classes(p):
    txt = open(p).read()
    out = {}
    cls = None
    for line in txt.splitlines():
        m = re.match(r'^class (\w+)\(', line)
        if m:
            cls = m.group(1); out[cls] = []
        elif cls and re.match(r'^    def test_\w+', line):
            out[cls].append(line.strip()[4:].split('(')[0])
    return out
old, new = classes('/tmp/opencode/old_tests.py'), classes('test/test_suite.py')
print('old classes: %d  tests: %d' % (len(old), sum(len(v) for v in old.values())))
print('new classes: %d  tests: %d' % (len(new), sum(len(v) for v in new.values())))
gone = [c for c in old if c not in new]
print('removed classes (%d):' % len(gone), ', '.join(sorted(gone)))
# any parser/graph/workspace method lost?
lost = [(c, m) for c, ms in old.items()
        for m in ms if c in new and m not in new[c]]
print('lost parser/graph/workspace methods:', lost or 'none')
PY
old classes: 45  tests: 305
new classes: 20  tests: 129
removed classes (25): TestAiClient, TestAiManager, TestAiPlayground, TestAiRoles,
  TestAiStandards, TestAiTeamBehavior, TestAiTeams, TestAiToolBuilder, TestAiTools,
  TestAiWorkflowTemplates, TestBuildCleaner, TestBuildMinifier, TestBuildPackager,
  TestBuildSandbox, TestMonitorInstrument, TestMonitorInstrumenter, TestMonitorPerf,
  TestMonitorProfiler, TestParserBenchmark, TestPinnedDeps, TestScoring,
  TestSideMetricsRecord, TestVersionManager, TestWorkspaceScoring, TestXpSystem
lost parser/graph/workspace methods: none
```

**Disposition table** — every removed test, grouped by what it covered:

| Removed class(es) | # tests | Disposition |
|---|---|---|
| `TestAiClient/Manager/Playground/Roles/Standards/TeamBehavior/Teams/ToolBuilder/Tools/WorkflowTemplates` | 148 | **Obsolete** — `ai/` deleted in the v0.6.0 rewrite (round 1 inventory, `ARCHITECTURE-DECISIONS.md`). |
| `TestBuildCleaner/Minifier/Packager/Sandbox` | 20 | **Obsolete** — `build/` deleted; Git now serves the packaging/versioning role. |
| `TestMonitorInstrument/Instrumenter/Profiler`, `TestParserBenchmark` | 17 | **Obsolete** — `monitor/` deleted; only `ParseTimer` survived (folded, below). |
| `TestVersionManager`, `TestPinnedDeps` | 4 | **Obsolete** — `version/` deleted; `versions` subcommands removed. |
| `TestScoring/WorkspaceScoring/XpSystem/SideMetricsRecord` | 4 | **Obsolete** — scoring/XP moved to `/api/xp`/`.side-metrics.json`; bridge recording covered by `TestSideNodeAdapter.test_record_xp_writes_metrics` (round 2). |
| `TestMonitorPerf` (`test_perf_timer...`) | 3 | **Folded** — `ParseTimer` was inlined into `parser/project_parser.py` and still ships `meta.perf` to the GUI, but had zero coverage. Re-added as `TestProjectParser.test_meta_perf_recorded`. |

Every parser/graph/workspace class survived with **identical method names** (0 lost). The 193
dropped tests are all subsystem-specific. The brief's "check `.gitignore` / `.side-*` /
`node_modules` / venv handling" found two genuinely untested `_should_ignore` rules, both
re-added to `TestWalker`:

```
$ python3 -m unittest discover -s test -p test_suite.py -k TestWalker -v 2>&1 | grep -E "venv|git_dir|Ran|OK"
test_git_dir_ignored (test_suite.TestWalker.test_git_dir_ignored) ... ok
test_venv_ignored (test_suite.TestWalker.test_venv_ignored) ... ok
Ran 8 tests in 0.004s
OK
$ python3 -m unittest discover -s test -p test_suite.py -k TestProjectParser -v 2>&1 | grep -E "meta_perf|Ran|OK"
test_meta_perf_recorded (test_suite.TestProjectParser.test_meta_perf_recorded) ... ok
Ran 4 tests in 0.105s
OK
```

**Coverage floor result:** not regressed by the rewrite — 17/17 parser/graph/workspace classes
preserved, 0 methods lost, 193 removed tests all subsystem-specific, 2 gaps re-closed. 305 −
193 (obsolete) − 3 (folded) = 109 equivalent pre-rewrite tests, vs 129 now.

## 3.1 — Restore the atrophied workspace features

`TestWorkspaceManifest` covered only `init`/`save`/`load`/`summary`/`add`/`remove`/
`requirements_txt` and one slow-path resolve test. Missing: direct `find_projects_in_workspace`,
the graph fast path for `resolve_project_deps`, module-level `add_package`, and
`_collect_external_imports` itself.

First, the fast path was **broken by the rewrite** and nothing covered it:

```
$ git show e772b8b^:parser/workspace.py | sed -n '274,284p'
        for node in graph.get("nodes", []):
            if node.get("isExternal"):
                # External nodes have IDs like "ext:requests"
                nid = node.get("id", "")
                if nid.startswith("ext:"):
                    imports.add(nid[4:])
        for edge in graph.get("edges", []):
            if edge.get("type") == "external":
                imports.add(edge.get("target", "").replace("ext:", ""))
```

The rewrite's `resolve_edges` (e772b8b) changed external edges to `target: "ext_<pkg>"` +
`externalPackage`, so the fast path returned `ext_requests`-style names that never matched a
workspace manifest — `resolve_project_deps` silently resolved nothing on any graph-backed
project. Fixed to read `externalPackage` first with `ext_`/`ext:` fallbacks. Verified against
this repo's real graph (fast path now resolves true externals):

```
$ python3 -c "
import json
from parser.workspace import _collect_external_imports
g = json.load(open('.nodegraph.json'))
fast = set(_collect_external_imports('.', g)); slow = set(_collect_external_imports('.', None))
print('fast:', sorted(fast)[:6], '... (%d pkgs)' % len(fast))
print('only-fast (stdlib externals):', sorted(fast-slow))
print('only-slow (local pkgs in scan):', sorted(slow-fast))"
fast: ['__future__', 'argparse', 'ast', 'collections', 'concurrent', 'contextlib'] ... (32 pkgs)
only-fast (stdlib externals): ['ctypes', 'queue', 'tomllib', 'webbrowser']
only-slow (local pkgs in scan): ['graph', 'parser', 'process']
```

The residual fast/slow difference is **pre-existing design, not a rewrite regression** — the
scan path never distinguished local from third-party imports, so it guesses `graph`/`parser`/
`process` (this repo's own packages) as "external". Fast path = `isExternal` edges only
(third-party + stdlib); scan path = all imports (fallback when no graph exists). Neither is
wrong for its purpose; aligning them means deciding whether local imports count as workspace
deps, which is flagged for round 4, not a round-3 call. Recorded in `ARCHITECTURE-DECISIONS.md`.

The four new tests (all temp-dir fixtures, no coupling to this repo's state):

```
$ python3 -m unittest discover -s test -p test_suite.py -k TestWorkspaceManifest -v 2>&1 | grep -v Warning
test_add_package (test_suite.TestWorkspaceManifest.test_add_package) ... ok
test_add_package_module_level (test_suite.TestWorkspaceManifest.test_add_package_module_level) ... ok
test_collect_external_imports_scan (test_suite.TestWorkspaceManifest.test_collect_external_imports_scan) ... ok
test_find_projects_in_workspace (test_suite.TestWorkspaceManifest.test_find_projects_in_workspace) ... ok
test_find_workspace_root (test_suite.TestWorkspaceManifest.test_find_workspace_root) ... ok
test_find_workspace_root_not_found (test_suite.TestWorkspaceManifest.test_find_workspace_root_not_found) ... ok
test_init_finds_projects (test_suite.TestWorkspaceManifest.test_init_finds_projects) ... ok
test_init_workspace (test_suite.TestWorkspaceManifest.test_init_workspace) ... ok
test_load_missing_returns_empty (test_suite.TestWorkspaceManifest.test_load_missing_returns_empty) ... ok
test_remove_package (test_suite.TestWorkspaceManifest.test_remove_package) ... ok
test_requirements_txt (test_suite.TestWorkspaceManifest.test_requirements_txt) ... ok
test_resolve_deps_from_imports (test_suite.TestWorkspaceManifest.test_resolve_deps_from_imports) ... ok
test_resolve_project_deps_uses_graph_external_edges (test_suite.TestWorkspaceManifest.test_resolve_project_deps_uses_graph_external_edges)
The graph fast path must match the real resolve_edges output shape ... ok
test_save_and_load (test_suite.TestWorkspaceManifest.test_save_and_load) ... ok
test_workspace_summary (test_suite.TestWorkspaceManifest.test_workspace_summary) ... ok
----------------------------------------------------------------------
Ran 15 tests in 0.005s
OK
```

`test_resolve_project_deps_uses_graph_external_edges` locks the `ext_`+`externalPackage`
shape and fails on the pre-fix code. Every public function in `parser/workspace.py`
(`find_workspace_root`, `find_projects_in_workspace`, `resolve_project_deps`,
`_collect_external_imports`, module-level `add_package`) is now called by at least one test.
Two pre-existing unclosed-file `ResourceWarning`s in this class fixed while in it.

## 3.2 — Parser → bridge integration

The 3.2 concern: round 2's `TestSideNodeAdapter` fed the handlers hand-built graph dicts —
hermetic, but idealized. The bridge's `childIds` basename-matching and `skillHints`
import-derivation had never run against **real** `parse_project` output. New test runs
`parse_project` on a synthetic project (app.py importing sibling `utils` + stdlib `os`, plus
a README), writes the real `.nodegraph.json`, and drives `Handler._get_nodes`:

```
$ python3 -m unittest discover -s test -p test_suite.py -k TestSideNodeAdapter -v 2>&1 | grep -E "real_parse|Ran|OK"
test_get_nodes_on_real_parse_output (test_suite.TestSideNodeAdapter.test_get_nodes_on_real_parse_output) ... ok
Ran 13 tests in 0.056s
OK
```

Assertions: `side:app_py` and `side:utils_py` exist; app's `childIds == ["side:utils"]` (the
sibling, present in the graph) with stdlib `os` **excluded**; `skillHints[:2] ==
["python", "backend"]` with `os` present as an import hint. This locks the parser's
`imports`/`path` conventions to the bridge's assumptions — the two halves MythOS depends on —
end to end. **No drift was exposed**: parser and bridge agree, so no `SideNode` change and
no parser change beyond the 3.1 workspace fix (which is parser-side and doesn't touch the
bridge shape).

## 3.3 — Close

Gate:

```
$ python3 main.py self-check .
[s-ide] 1/3: Running unit tests...
[s-ide] OK: Tests passed.                        (129)
[s-ide] 2/3: Parsing graph & auditing docs...
[s-ide] OK: Parsing complete (131.08 ms) → /home/n0v4/DevOps/Native/s-ide/.nodegraph.json
[s-ide] 3/3: Document report:
  Missing READMEs: 1
  Stale READMEs:   3
  Empty modules:   1
[s-ide] Doc health issues detected.
[s-ide] OK: Continuing (non-fatal docs).
[s-ide] SUMMARY: ALL CHECKS PASSED.
EXIT=0
```

Commits (one item, one commit):

```
$ git log --oneline -4
<close commit>  docs(rounds): close round 3 — audit, round-4 brief, roadmap status
340e526         test(bridge): exercise parser→bridge end to end on real parse output
1620a2e         fix(workspace): fast path matched the pre-rewrite ext: shape, not real graphs
537022b         test(parser): close coverage-floor gaps — walker ignore rules, meta.perf
```

`git status --short` is clean; branch `main` up to date with `origin/main`, **not pushed**
(stop condition 6). 121 tests at round 2 close → 129 now (+2 walker, +1 meta.perf, +4
workspace, +1 integration).

## Report to Nova

- **What passed:** everything. Coverage floor verified numerically (17/17 parser/graph/workspace
  classes preserved with identical method names; 193 removed tests all ai/build/monitor/version;
  disposition table above). Two real gaps found and closed — walker venv/`.git` ignore rules and
  the inlined `ParseTimer`/`meta.perf` (zero coverage despite shipping to the GUI). Workspace
  features fully restored with tests; **and the fast path was genuinely broken by the rewrite**
  (stripped `ext:`, the pre-rewrite prefix; real graphs use `ext_`+`externalPackage`) — the
  fix's test fails on the old code. Parser→bridge integration confirmed on real `parse_project`
  output: no drift. `self-check` exits 0, 129 tests green.
- **What's still red, and why:** nothing functional. `self-check` still reports 1 missing /
  3 stale READMEs (non-fatal, unchanged since round 1).
- **Parser-shape change:** none to the bridge. The workspace fix is parser-side; the `SideNode`
  shape is untouched. Two Python 3.13 `DeprecationWarning`s (`ast.Constant.s`, multiprocessing
  fork) surfaced in `self-check` output — pre-existing, from `python_parser.py:248`, out of
  round 3's scope; flagged for the round-4 cleanup.
- **Stop conditions:** none fired. No new dependencies, no `SideNode` shape/type changes, no
  network calls, nothing deleted, `self-check` passed once, no push.
- **Flagged for round 4:** the fast/slow `_collect_external_imports` divergence (whether local
  imports count as workspace deps); the `DeprecationWarning`s; plus the existing round-4 items
  (dead-code sweep, README architecture diagram vs reality, `ai/teams.py` fate).
