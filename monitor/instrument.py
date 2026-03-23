# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
monitor/instrument.py
=====================
Lightweight instrumentation that any project can import to report
per-function and per-module timing back to S-IDE.

How it works
------------
Projects call `instrument.trace(path)` or use the `@timed` decorator.
Timing data is written to a small JSON file that S-IDE polls:

    <project_root>/.side-metrics.json

S-IDE reads this file every 2 seconds and overlays the data on node
cards in the graph.  No network, no subprocess protocol — just a file.

Usage in any project
--------------------
    # Option 1: decorator (per-function timing)
    from monitor.instrument import timed

    @timed
    def process_data(records):
        ...

    # Option 2: context manager (per-block timing)
    from monitor.instrument import timed_block

    with timed_block("build_index"):
        index = {r.id: r for r in records}

    # Option 3: auto-trace an entire module on import
    from monitor.instrument import trace_module
    trace_module(__file__)   # put at bottom of any .py file

    # Flush metrics to disk manually (auto-flushes every 5s)
    from monitor.instrument import flush
    flush()

Metrics file format (.side-metrics.json)
-----------------------------------------
{
  "pid":       12345,
  "updated":   1700000000.0,
  "files": {
    "src/parser.py": {
      "calls": 42,
      "total_ms": 1234.5,
      "avg_ms": 29.4,
      "max_ms": 201.0,
      "last_ms": 28.1,
      "last_ts": 1700000000.0
    }
  },
  "functions": {
    "src/parser.py::parse_file": {
      "calls": 42,
      "total_ms": 1100.0,
      "avg_ms": 26.2,
      "max_ms": 190.0,
      "last_ms": 25.0,
      "last_ts": 1700000000.0
    }
  }
}

S-IDE matches "files" keys against its node relative paths.
"""

from __future__ import annotations
import functools
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Callable

# ── Metrics store ──────────────────────────────────────────────────────────────

_lock    = threading.Lock()
_metrics = {
    "pid":       os.getpid(),
    "updated":   time.time(),
    "files":     {},      # rel_path → aggregated stats
    "functions": {},      # rel_path::func_name → aggregated stats
}
_project_root: str = ""
_metrics_path: str = ""
_flush_interval   = 5.0    # seconds between auto-flushes
_flush_thread: threading.Thread | None = None


def _rel_path(filepath: str) -> str:
    """Convert an absolute path to a project-relative path."""
    if _project_root:
        try:
            return os.path.relpath(filepath, _project_root).replace("\\", "/")
        except ValueError:
            pass
    return os.path.basename(filepath)


def _record(filepath: str, func_name: str | None, elapsed_ms: float) -> None:
    """Record a timing sample into the in-memory store."""
    rel = _rel_path(filepath)
    now = time.time()

    def _update(store: dict, key: str) -> None:
        if key not in store:
            store[key] = {"calls": 0, "total_ms": 0.0,
                          "avg_ms": 0.0, "max_ms": 0.0,
                          "last_ms": 0.0, "last_ts": 0.0}
        s = store[key]
        s["calls"]    += 1
        s["total_ms"] += elapsed_ms
        s["avg_ms"]    = s["total_ms"] / s["calls"]
        s["max_ms"]    = max(s["max_ms"], elapsed_ms)
        s["last_ms"]   = elapsed_ms
        s["last_ts"]   = now

    with _lock:
        _update(_metrics["files"], rel)
        if func_name:
            _update(_metrics["functions"], f"{rel}::{func_name}")
        _metrics["updated"] = now


def _flush_loop() -> None:
    while True:
        time.sleep(_flush_interval)
        flush()


def flush() -> None:
    """Write current metrics to .side-metrics.json immediately."""
    if not _metrics_path:
        return
    with _lock:
        snapshot = {
            "pid":       _metrics["pid"],
            "updated":   _metrics["updated"],
            "files":     dict(_metrics["files"]),
            "functions": dict(_metrics["functions"]),
        }
    try:
        tmp = _metrics_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp, _metrics_path)
    except OSError:
        pass


def init(project_root: str, flush_interval: float = 5.0) -> None:
    """
    Initialise the instrumentation for a project.
    Call once at the start of your application.

    project_root : absolute path to the project directory
    flush_interval : how often to write metrics to disk (seconds)
    """
    global _project_root, _metrics_path, _flush_interval, _flush_thread
    _project_root  = os.path.abspath(project_root)
    _metrics_path  = os.path.join(_project_root, ".side-metrics.json")
    _flush_interval = flush_interval
    _metrics["pid"] = os.getpid()

    if _flush_thread is None or not _flush_thread.is_alive():
        _flush_thread = threading.Thread(
            target=_flush_loop, name="side-instrument", daemon=True
        )
        _flush_thread.start()


# ── Public API ─────────────────────────────────────────────────────────────────

def timed(func: Callable) -> Callable:
    """
    Decorator: time every call to func and report to S-IDE.

    @timed
    def expensive():
        ...
    """
    filepath = sys.modules.get(func.__module__, None)
    fpath    = getattr(filepath, "__file__", "") or ""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        t0 = time.monotonic()
        try:
            return func(*args, **kwargs)
        finally:
            _record(fpath, func.__qualname__,
                    (time.monotonic() - t0) * 1000)
    return wrapper


@contextmanager
def timed_block(label: str, filepath: str = ""):
    """
    Context manager: time a block of code.

    with timed_block("build_index", __file__):
        index = {r.id: r for r in records}
    """
    if not filepath:
        # Try to infer from call stack
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            filepath = frame.f_back.f_code.co_filename
    t0 = time.monotonic()
    try:
        yield
    finally:
        _record(filepath, label, (time.monotonic() - t0) * 1000)


def trace_module(filepath: str) -> None:
    """
    Register a module for timing without decorating individual functions.
    Records every call via sys.settrace — use sparingly (adds overhead).
    This is a simpler version: just records a module-level "import" timing.

    Put at the bottom of any .py file:
        from monitor.instrument import trace_module
        trace_module(__file__)
    """
    # Record that this module was loaded/re-executed
    _record(filepath, None, 0.0)


def reset() -> None:
    """Clear all accumulated timing data."""
    with _lock:
        _metrics["files"].clear()
        _metrics["functions"].clear()
        _metrics["updated"] = time.time()


def get_snapshot() -> dict:
    """Return a copy of the current metrics without flushing."""
    with _lock:
        return {
            "pid":       _metrics["pid"],
            "updated":   _metrics["updated"],
            "files":     dict(_metrics["files"]),
            "functions": dict(_metrics["functions"]),
        }

# ── GPLv3 interactive notice ──────────────────────────────────────────────────

_GPLv3_WARRANTY = (
    "THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY\n"
    "APPLICABLE LAW. EXCEPT WHEN OTHERWISE STATED IN WRITING THE COPYRIGHT\n"
    'HOLDERS AND/OR OTHER PARTIES PROVIDE THE PROGRAM \"AS IS\" WITHOUT\n'
    "WARRANTY OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT\n"
    "LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A\n"
    "PARTICULAR PURPOSE. THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE\n"
    "OF THE PROGRAM IS WITH YOU.  (GPL-3.0-or-later §15)"
)

_GPLv3_CONDITIONS = (
    "You may convey verbatim copies of the Program's source code as you\n"
    "receive it, in any medium, provided that you conspicuously and\n"
    "appropriately publish on each copy an appropriate copyright notice and\n"
    "disclaimer of warranty. (See GPL-3.0 §4-6 for full conditions.)\n"
    "Full license: <https://www.gnu.org/licenses/gpl-3.0.html>"
)


def gplv3_notice():
    """Print the short GPLv3 startup notice. Call this at program startup."""
    print("S-IDE  Copyright (C) 2026  N0V4-N3XU5")
    print("This program comes with ABSOLUTELY NO WARRANTY; for details type 'show w'.")
    print("This is free software, and you are welcome to redistribute it")
    print("under certain conditions; type 'show c' for details.")


def gplv3_handle(cmd: str) -> bool:
    """
    Check whether *cmd* is a GPLv3 license command and handle it.
    Returns True if the command was consumed (caller should skip normal processing).
    """
    match cmd.strip().lower():
        case "show w":
            print(_GPLv3_WARRANTY)
            return True
        case "show c":
            print(_GPLv3_CONDITIONS)
            return True
    return False

# ─────────────────────────────────────────────────────────────────────────────
