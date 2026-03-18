# build/

Clean, minify, and package S-IDE projects for distribution.

## Modules

### cleaner.py — `clean_project(root, opts)`

Removes dev artifacts in tiers you can pick independently:

| Tier | What gets removed |
|---|---|
| `cache` | `__pycache__`, `*.pyc`, `.mypy_cache`, `.pytest_cache` |
| `logs` | `logs/`, `*.log` |
| `build` | `dist/`, `build/`, `*.egg-info/`, `.nodegraph.json` |
| `dev` | test data, scratch dirs, `.coverage`, `htmlcov/` |
| `all` | everything above |

`dry_run=True` reports what would be removed without deleting anything.

### minifier.py — `minify_project(src, dst, opts)`

Strips comments, docstrings, and blank lines. Per-language:
- **Python**: uses `ast` for accurate docstring removal, preserves `# noqa` / `# type: ignore`
- **JS/TS**: regex on comment-stripped source
- **JSON**: `json.dumps` with no whitespace
- **Shell**: strips `#` comments, blank lines, keeps shebang

`bundle_modules(graph, src, out_path)` combines Python modules into a single file in topological dependency order.

### packager.py — `package_project(root, out_dir, opts)`

Three output kinds:

| Kind | Output |
|---|---|
| `tarball` | `.tar.gz` of minified source — compatible with S-IDE update system |
| `installer` | `.tar.gz` (Linux/macOS) or `.zip` (Windows) with launcher scripts |
| `portable` | Self-contained directory, optionally with bundled `.venv` |

Writes a `build-manifest.json` in `out_dir` tracking the last 20 builds.

## Full pipeline example

```bash
# Via CLI
python main.py build . --kind tarball --bump minor

# Or programmatically
from build import clean_project, minify_project, package_project
from build import CleanOptions, MinifyOptions, PackageOptions

clean_project(".", CleanOptions(tiers=["cache", "logs"]))
result = package_project(".", "dist",
    PackageOptions(kind="tarball", minify=True, clean=False))
print(result.summary())
```
