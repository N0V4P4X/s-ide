# monitor/

Runtime performance monitoring for S-IDE and any project it loads.

## perf.py

### ParseTimer
Times each stage of `parse_project`. Results stored in `graph.meta.perf` and displayed as a bar chart in the Build tab.

```python
from monitor.perf import ParseTimer
with ParseTimer() as t:
    with t.stage("parse_files"):
        parse_all_files(nodes)
    with t.stage("resolve_edges"):
        edges = resolve(nodes)
print(t.report())   # {"total_ms": 268, "slowest": "parse_files", "stages": [...]}
```

### ProcessMonitor
Samples CPU% and RSS memory for every `ManagedProcess` every 2 seconds. Uses `psutil` if installed, falls back to `/proc/<pid>/status` on Linux.

### MetricsWatcher
Polls `<project>/.side-metrics.json` written by `instrument.py`. Background thread, detects changes by mtime, exposes per-file and per-function timing to the GUI for node-card overlays.

```python
from monitor.perf import MetricsWatcher
watcher = MetricsWatcher("/path/to/project")
watcher.start()
watcher.get_file_metrics()      # {rel_path: {calls, avg_ms, max_ms, …}}
watcher.get_function_metrics()  # {rel_path::fn: {…}}
watcher.stop()
```

## instrument.py

Lightweight instrumentation that any project can import.

```python
from monitor.instrument import init, timed, timed_block

init("/path/to/your/project")   # optional, defaults to cwd

@timed
def parse_file(path: str) -> dict:
    ...

with timed_block("database_query", __file__):
    results = db.execute(query)
```

Data is flushed to `.side-metrics.json` every 5 seconds. S-IDE detects the change and updates node cards within ~1.5 seconds.

**Node card display:**
- Colour strip: green (<10ms avg), amber (<100ms), red (>100ms), grey (stale)
- Badge: `42× 28ms avg`
- Inspector: full breakdown per function

## instrumenter.py

Bulk-instruments a project directory — adds `@timed` to every public function.

```python
from monitor.instrumenter import Instrumenter, InstrumentOptions
result = Instrumenter("/path/to/project", InstrumentOptions(
    public_only=True,
    add_tests=True,
    backup=True,         # enables rollback
    preview=False,
)).run()
print(result.summary())
# Rollback:
from monitor.instrumenter import rollback
rollback("/path/to/project")
```

Options: `public_only`, `top_level_only`, `skip_dunders`, `min_lines`, `add_tests`, `backup`, `preview`.
