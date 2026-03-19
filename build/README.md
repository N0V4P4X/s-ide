# build/

Clean, minify, and package projects for distribution.

## Modules

### cleaner.py

```python
from build.cleaner import clean_project, CleanOptions
clean_project(".", CleanOptions(tiers=["cache", "logs"]))
```

| Tier | Removes |
|---|---|
| `cache` | `__pycache__`, `*.pyc`, `.mypy_cache`, `.pytest_cache` |
| `logs` | `logs/`, `*.log` |
| `build` | `dist/`, `build/`, `*.egg-info/`, `.nodegraph.json` |
| `dev` | test data, scratch dirs, `.coverage`, `htmlcov/` |

`CleanOptions(dry_run=True)` reports without deleting.

### minifier.py

```python
from build.minifier import minify_project, MinifyOptions
minify_project(src=".", dst="dist/", opts=MinifyOptions(strip_docstrings=True))
```

- **Python**: AST-based docstring removal, preserves `# noqa`/`# type: ignore`
- **JS/TS**: regex comment stripping
- **JSON**: `json.dumps` with no whitespace
- **Shell**: strips `#` comments, keeps shebang

`bundle_modules(graph, src, out_path)` combines Python modules into a single file in topological dependency order — the foundation for Phase 3 optimization output.

### packager.py

```python
from build.packager import package_project, PackageOptions
result = package_project(".", "dist", PackageOptions(kind="tarball", minify=True))
print(result.summary())
```

| Kind | Output |
|---|---|
| `tarball` | `.tar.gz` — compatible with S-IDE update system |
| `installer` | `.tar.gz`/`.zip` with launcher scripts |
| `portable` | Self-contained directory, optionally with `.venv` |

Writes `build-manifest.json` tracking the last 20 builds.

### sandbox.py

Runs a project in an isolated temp copy, applies clean/minify transforms, captures logs.

```python
from build.sandbox import SandboxRun, SandboxOptions
sb = SandboxRun(root, SandboxOptions(mode="clean", keep_log_runs=3))
sb.prepare()     # copy to tempdir, apply transforms
sb.start("python main.py")
sb.cleanup()     # copy logs to logs/sandbox/<ts>/, delete tempdir
```

## Phase 3 role

The build pipeline is the execution layer for the optimization workflow described in `FUTURE.md`. `minifier.py` handles mechanical stripping; `bundler` handles import resolution; `sandbox.py` provides isolated verification runs. The optimizer connects these with measurement and verification gates.
