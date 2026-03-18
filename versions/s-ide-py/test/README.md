# S-IDE Test Suite

`test/test_suite.py` — 65 unit tests covering every module. Uses stdlib `unittest` only, no pytest required (though pytest works fine too).

## Running

```bash
# From the s-ide-py/ root directory:
python test/test_suite.py

# Verbose output (shows each test name as it runs):
python test/test_suite.py -v

# Via the run system:
python main.py run . test
```

Expected output when everything passes:
```
Ran 65 tests in ~3s
OK
```

## Test Groups

| Class | What it covers |
|---|---|
| `TestPythonParser` | AST-based import/export/definition extraction, fallback on syntax errors |
| `TestJSParser` | ES imports, CJS require, exports, comment stripping |
| `TestJSONParser` | package.json, tsconfig, malformed JSON |
| `TestShellParser` | source/export/function, tags (docker, systemd…) |
| `TestWalker` | Directory traversal, ignore patterns, hidden files, node IDs |
| `TestProjectConfig` | side.project.json load/save/init, version bumping |
| `TestResolveEdges` | Relative imports → edges, external packages, deduplication |
| `TestLayout` | Topological position assignment, orphan nodes, empty graph |
| `TestDocCheck` | Missing/stale README detection, empty module warnings |
| `TestProjectParser` | Full pipeline on a synthetic project, edge resolution, JSON output |
| `TestVersionManager` | Archive/extract/list/apply-update, versions dir excluded from archive |
| `TestProcessManager` | Spawn, log capture, stop, stdout callback, purge |

## Troubleshooting parse issues

The test suite is the first place to check when the parser behaves unexpectedly. Each test creates its own isolated temp directory, so failures point directly at the broken module.

**If the GUI freezes on parse**, run the parser standalone to see exactly where it hangs:

```bash
python main.py parse /path/to/your/project
```

This prints node/edge counts and parse time to stdout without any GUI involved. If it hangs here too, the issue is in the parser, not the GUI.

**To get logs from a GUI session**, the log file is written to `~/.s-ide.log`. The path is always printed to the terminal when you launch:

```bash
python gui/app.py
# → [s-ide] S-IDE session started — log: /home/yourname/.s-ide.log
```

Tail it in a second terminal while the app runs:

```bash
tail -f ~/.s-ide.log
```

The in-app **LOG** button in the topbar opens a live log panel with the same content (auto-refreshes every 2 seconds).

## Adding tests

Each test class follows the same pattern — inherit `unittest.TestCase`, use `_tmp_project()` for any test that needs real files:

```python
class TestMyNewParser(unittest.TestCase):

    def test_something(self):
        with _tmp_project(
            ("src/main.py", "import os\n"),
        ) as tmp:
            files = walk_directory(tmp)
            self.assertEqual(len(files), 1)
```

`_tmp_project(*files)` takes `(relative_path, content)` pairs, creates them in a temp directory, and cleans up automatically when the `with` block exits.
