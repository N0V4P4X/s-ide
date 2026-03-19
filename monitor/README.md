# monitor/

Runtime performance monitoring for S-IDE and the projects it loads.

## Modules

```
monitor/
├── profiler.py     — cProfile-based project profiler (the main one to use)
├── perf.py         — ParseTimer, ProcessMonitor, MetricsWatcher
├── instrument.py   — @timed decorator (legacy, prefer profiler.py)
└── instrumenter.py — Bulk @timed instrumentation (legacy)
```

---

## profiler.py — Live project profiler

Profiles a project's **actual execution** using `cProfile`. Writes results to
`.side-metrics.json` so the graph canvas shows live timing overlays on node
cards immediately. No code modification required.

```python
from monitor.profiler import profile_project, profile_function, load_last_profile

# Profile the project entry point (auto-detected if not specified)
result = profile_project(
    project_root = "/path/to/myproject",
    entry_point  = "src/main.py",   # optional — auto-detected
    args         = ["--input", "data.csv"],
    timeout      = 60,
)
print(result.summary())
# Profile: src/main.py
# Total:   843ms  exit=0
# Top functions (by cumulative time):
#   parse_file             412.3ms  847x  [src/parser.py]
#   tokenise                98.1ms  847x  [src/parser.py]
#   ...

# Profile a single function
result = profile_function(
    project_root  = "/path/to/myproject",
    module_path   = "src/parser.py",
    function_name = "parse_file",
    call_args     = "'test_input.txt'",
)

# Load the last profile run
data = load_last_profile("/path/to/myproject")
# data = {"files": {...}, "functions": {...}, "total_ms": ...}
```

### How it works

1. Entry point runs under `cProfile` in a subprocess sandbox
2. `pstats` output parsed into per-file and per-function timing
3. Written to `<root>/.side-metrics.json` — same format as `MetricsWatcher`
4. `MetricsWatcher` detects the file change and updates node cards (~1.5s)

### GUI usage

Click **⏱ Profile** in the topbar. The project's entry point is auto-detected
from `side.project.json` run scripts, or falls back to `src/main.py` / `main.py`.
Results appear in AI Chat with the top 10 functions, and node cards update
with colour-coded timing strips.

### Manager tool

The Manager bot can trigger profiling directly:

```
User: Profile the calculator project
Manager: [calls profile_project(entry_point="src/cli.py")]
         → writes .side-metrics.json, reports top functions
```

### `.side-metrics.json` format

```json
{
  "pid": 12345,
  "updated": 1700000000.0,
  "profiled": "src/main.py",
  "total_ms": 843.2,
  "files": {
    "src/parser.py": {
      "calls": 847, "total_ms": 510.4,
      "avg_ms": 0.60, "max_ms": 2.1, "last_ms": 0.58
    }
  },
  "functions": {
    "src/parser.py::parse_file": {
      "calls": 847, "total_ms": 412.3, "avg_ms": 0.49,
      "own_ms": 198.1, "per_call_ms": 0.49
    }
  }
}
```

---

## perf.py — Parse pipeline + process monitoring

### ParseTimer
Times each stage of `parse_project`. Results stored in `graph.meta.perf`.

```python
from monitor.perf import ParseTimer
timer = ParseTimer()
with timer.stage("walk"):
    files = walk_directory(root)
with timer.stage("parse_files"):
    ...
report = timer.report()  # {"total_ms": 268, "stages": [...]}
```

### ProcessMonitor
Samples CPU% and RSS for `ManagedProcess` every 2s.

### MetricsWatcher
Polls `.side-metrics.json` (written by `profiler.py` or `instrument.py`).
Background thread, detects changes by mtime, feeds the GUI node overlays.

---

## instrument.py — @timed decorator (legacy)

The original approach: add `@timed` to functions so they push timing data
at runtime. Still works, but **`profiler.py` is preferred** — it requires no
code modification and gives more accurate results via cProfile sampling.

```python
from monitor.instrument import init, timed, timed_block
init("/path/to/project")

@timed
def parse_file(path):
    ...
```

Use `profiler.py` for new projects. The `@timed` approach is useful when you
want persistent timing across many runs without re-profiling.

---

## instrumenter.py — Bulk instrumentation (legacy)

Adds `@timed` to every public function in a project directory.
Has backup/rollback support. Prefer `profiler.py` for one-off profiling.
