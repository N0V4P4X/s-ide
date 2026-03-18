# monitor/

Runtime performance monitoring for S-IDE and any project it loads.

## perf.py

### ParseTimer
Times each stage of `parse_project` with context managers. Results live in `graph.meta.perf` and are displayed as a bar chart in the BUILD panel.

### ProcessMonitor
Samples CPU% and RSS memory for every `ManagedProcess` every 2 seconds using `psutil` (if installed) or `/proc/<pid>/status` on Linux. The PROC panel shows live sparklines per process.

### MetricsWatcher
Polls `<project_root>/.side-metrics.json` written by `monitor/instrument.py`. Runs as a background thread, detects file changes by mtime, and exposes per-file and per-function timing to the GUI for node-card overlays.

```python
from monitor.perf import MetricsWatcher
watcher = MetricsWatcher("/path/to/project")
watcher.start()
file_metrics = watcher.get_file_metrics()   # {rel_path: {calls, avg_ms, ...}}
fn_metrics   = watcher.get_function_metrics()  # {rel_path::fn: {...}}
watcher.stop()
```

---

## instrument.py

Lightweight instrumentation that **any project** can import to push live timing data back to S-IDE.

### How it works

1. Your project imports `monitor.instrument` and decorates functions with `@timed`
2. Every call records elapsed milliseconds in memory
3. Every 5 seconds (configurable), data is flushed to `.side-metrics.json` in your project root
4. S-IDE's `MetricsWatcher` detects the file change and updates node cards within ~1.5 seconds

### Setup in your project

```python
# In your project's entry point (main.py, __init__.py, etc.)
from monitor.instrument import init, timed, timed_block

# Point at your project root (optional — defaults to cwd)
init("/path/to/your/project")

# Decorate functions you want to time
@timed
def parse_file(path):
    ...

@timed
def build_index(records):
    ...

# Or time a block
with timed_block("database_query", __file__):
    results = db.execute(query)
```

### What you'll see in S-IDE

Each node card for a timed file shows:

- **Colour-coded bottom strip**: green (fast, <10ms avg), amber (<100ms), red (>100ms), grey (stale >8s)
- **Badge**: `42× 28ms avg` — call count and average time
- **Inspector panel** (click the node): full breakdown with calls/avg/max/last + per-function table

### Instrumenting S-IDE itself

S-IDE already instruments its own parser via `ParseTimer`. To see live timing while S-IDE is running and monitoring itself:

```python
# Add to any parser file, e.g. parser/project_parser.py
from monitor.instrument import init, timed
init(__file__)   # finds project root automatically

@timed
def parse_project(root_dir, save_json=True):
    ...
```

### API

```python
init(project_root, flush_interval=5.0)  # call once at startup
timed                                    # decorator
timed_block(label, filepath="")         # context manager
trace_module(__file__)                   # record module load
flush()                                  # force immediate write
reset()                                  # clear all data
get_snapshot()                           # dict without flushing
```

### .side-metrics.json format

```json
{
  "pid": 12345,
  "updated": 1700000000.0,
  "files": {
    "src/parser.py": {
      "calls": 42, "total_ms": 1234.5,
      "avg_ms": 29.4, "max_ms": 201.0,
      "last_ms": 28.1, "last_ts": 1700000000.0
    }
  },
  "functions": {
    "src/parser.py::parse_file": { ... }
  }
}
```
