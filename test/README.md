# test/

`test_suite.py` — 184 tests, 33 classes, stdlib `unittest`. No pytest required.

## Running

```bash
python test/test_suite.py          # run all tests
python test/test_suite.py -v       # verbose (test names as they run)
python main.py run . test          # via run system
```

Expected: `Ran 184 tests in ~5s — OK`

## Test classes

| Class | What it covers |
|---|---|
| `TestPythonParser` | AST import/export/definition extraction, syntax-error fallback |
| `TestPythonParserDataFlow` | args, return_type, calls, raises, complexity, end_line |
| `TestJSParser` | ES imports, CJS require, exports, TS types |
| `TestJSONParser` | package.json, tsconfig, malformed JSON |
| `TestShellParser` | source/., exports, function defs, tags |
| `TestTomlParser`, `TestYamlParser` | config file parsing |
| `TestWalker` | Directory traversal, ignore patterns, hidden files |
| `TestProjectConfig` | side.project.json load/save/init/bump |
| `TestResolveEdges` | Relative imports → edges, externals, deduplication |
| `TestLayout` | Topological position assignment, orphans, empty graph |
| `TestDocCheck` | Missing/stale README, empty module warnings |
| `TestProjectParser` | Full pipeline on a synthetic project |
| `TestVersionManager`, `TestVersionManagerBootstrap` | Archive, extract, list, update |
| `TestProcessManager` | Spawn, log capture, stop, callbacks |
| `TestParseTimer` | Stage timing, JSON output |
| `TestCleaner` | Tier-based artifact removal, dry run, protect list |
| `TestMinifier` | Comment/docstring stripping, bundle_modules |
| `TestPackager` | tarball/installer/portable, manifest |
| `TestInstrument` | @timed decorator, flush, reset, stats |
| `TestMetricsWatcher` | mtime polling, get_file_metrics, get_function_metrics |
| `TestInstrumenter` | Bulk instrumentation, backup, rollback, test stubs |
| `TestSandbox` | Clean/minified sandbox, log retention |
| `TestAIClient` | ChatMessage, ChatResponse, ToolResult, OllamaClient (offline) |
| `TestAITools` | read_file, list_files, search_definitions, git, context |
| `TestMarkdownRenderer` | ai_append_markdown, _insert_inline (headless) |
| `TestSessionState` | add_project, ai_history, terminal_history, viewport |
| `TestGitTool` | git status/log dispatch |
| `TestFilterLogic` | Multi-select category filter, hidden_cats, show_ext |
| `TestDocLinks` | Directory-based doc→source matching |

## Adding tests

All tests use `tempfile.TemporaryDirectory()` for filesystem work — never write to the real project tree. Import the module under test directly, not via the `gui` package (which would trigger tkinter).

```python
class TestMyModule(unittest.TestCase):
    def test_something(self):
        with tempfile.TemporaryDirectory() as tmp:
            # write test files into tmp
            # call the function under test
            # assert results
```
