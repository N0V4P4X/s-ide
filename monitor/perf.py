# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
monitor/perf.py
===============
Performance monitoring for S-IDE.

Tracks two categories of metrics:

1. Parse pipeline timing
   Each stage of parse_project (walk, per-file parse, edge resolution,
   layout, doc-check) is timed and stored in the graph JSON under
   meta.perf.  The GUI displays this as a pipeline breakdown.

2. Process resource usage
   For each ManagedProcess, samples CPU % and RSS memory every N seconds
   using /proc/<pid>/stat (Linux) or psutil if available.  Stored in a
   rolling window so the GUI can plot a live sparkline.

Usage
-----
    # Wrapping the parser:
    from monitor.perf import ParseTimer
    timer = ParseTimer()
    with timer.stage("walk"):
        files = walk_directory(root)
    with timer.stage("parse_files"):
        ...
    report = timer.report()   # → dict ready for graph meta

    # Process sampling:
    from monitor.perf import ProcessMonitor
    mon = ProcessMonitor(proc_mgr)
    mon.start()               # background thread, samples every 2s
    mon.stop()
    snapshot = mon.snapshot() # → {proc_id: {cpu, rss_mb, samples: [...]}}
"""

from __future__ import annotations
import os
import sys
import time
import threading
from collections import deque
from contextlib import contextmanager
from typing import Iterator


# ── Parse pipeline timer ──────────────────────────────────────────────────────

class ParseTimer:
    """
    Context-manager based stage timer for the parse pipeline.

    with timer.stage("walk"):
        ...
    with timer.stage("per_file"):
        ...
    report = timer.report()
    """

    def __init__(self) -> None:
        self._stages: list[tuple[str, float]] = []   # (name, elapsed_ms)
        self._start = time.monotonic()
        self._stage_start: float | None = None
        self._current: str | None = None

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t0 = time.monotonic()
        self._current = name
        try:
            yield
        finally:
            elapsed = (time.monotonic() - t0) * 1000
            self._stages.append((name, round(elapsed, 2)))
            self._current = None

    def total_ms(self) -> float:
        return round((time.monotonic() - self._start) * 1000, 2)

    def report(self) -> dict:
        """Return a dict suitable for embedding in graph meta.perf."""
        return {
            "total_ms":  self.total_ms(),
            "stages":    [{"name": n, "ms": ms} for n, ms in self._stages],
            "slowest":   max(self._stages, key=lambda x: x[1])[0] if self._stages else None,
        }


# ── Process resource sampler ──────────────────────────────────────────────────

_HAVE_PSUTIL = False
try:
    import psutil  # type: ignore
    _HAVE_PSUTIL = True
except ImportError:
    pass


def _sample_pid_linux(pid: int) -> tuple[float, float] | None:
    """
    Sample a process on Linux using /proc/<pid>/stat.
    Returns (cpu_percent_approx, rss_mb) or None if the process is gone.
    cpu_percent here is a rough delta over the sampling interval.
    """
    try:
        stat_path = f"/proc/{pid}/stat"
        status_path = f"/proc/{pid}/status"
        if not os.path.exists(stat_path):
            return None

        # RSS from /proc/pid/status
        rss_kb = 0
        with open(status_path) as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    break

        return (0.0, round(rss_kb / 1024, 2))  # cpu% needs two samples
    except (OSError, ValueError):
        return None


def _sample_pid(pid: int) -> tuple[float, float] | None:
    """
    Return (cpu_percent, rss_mb) for a pid.
    Uses psutil if available, else Linux /proc fallback, else None.
    """
    if _HAVE_PSUTIL:
        try:
            p = psutil.Process(pid)
            cpu = p.cpu_percent(interval=None)
            rss = round(p.memory_info().rss / 1024 / 1024, 2)
            return (cpu, rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
    if sys.platform.startswith("linux"):
        return _sample_pid_linux(pid)
    return None


class ProcessMonitor:
    """
    Background sampler for all processes in a ProcessManager.

    Stores a rolling window of (timestamp, cpu%, rss_mb) tuples per
    process ID.  The GUI reads snapshot() to render sparklines.
    """

    SAMPLE_INTERVAL = 2.0    # seconds between samples
    WINDOW_SIZE     = 120    # samples kept per process (~4 min at 2s)

    def __init__(self, proc_mgr) -> None:
        self._mgr    = proc_mgr
        self._data:  dict[str, deque] = {}   # proc_id → deque of dicts
        self._lock   = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        # Prime psutil cpu_percent (first call always returns 0)
        if _HAVE_PSUTIL:
            for info in proc_mgr.list():
                if info.get("pid"):
                    try:
                        psutil.Process(info["pid"]).cpu_percent(interval=None)
                    except Exception:
                        pass

    def start(self) -> None:
        """Start background sampling thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="proc-monitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.wait(timeout=self.SAMPLE_INTERVAL):
            self._sample_all()

    def _sample_all(self) -> None:
        for info in self._mgr.list():
            pid = info.get("pid")
            proc_id = info.get("id")
            if not pid or not proc_id:
                continue
            result = _sample_pid(pid)
            if result is None:
                continue
            cpu, rss = result
            entry = {
                "ts":  round(time.time(), 1),
                "cpu": cpu,
                "rss": rss,
            }
            with self._lock:
                if proc_id not in self._data:
                    self._data[proc_id] = deque(maxlen=self.WINDOW_SIZE)
                self._data[proc_id].append(entry)

    def snapshot(self) -> dict:
        """
        Return current metrics for all sampled processes.

        {
          proc_id: {
            "latest": {"cpu": float, "rss": float, "ts": float},
            "samples": [{"ts":..., "cpu":..., "rss":...}, ...]
          }
        }
        """
        with self._lock:
            result = {}
            for proc_id, window in self._data.items():
                samples = list(window)
                if not samples:
                    continue
                result[proc_id] = {
                    "latest":  samples[-1],
                    "samples": samples,
                }
            return result

    def latest(self, proc_id: str) -> dict | None:
        """Return the most recent sample for a process, or None."""
        with self._lock:
            window = self._data.get(proc_id)
            if window:
                return window[-1]
        return None


# ── Metrics file watcher ──────────────────────────────────────────────────────

class MetricsWatcher:
    """
    Polls <project_root>/.side-metrics.json written by monitor/instrument.py.

    Any running project that imports monitor.instrument will write timing
    data for its source files there.  This class reads it and makes the
    data available to the GUI for overlay on node cards.

    The watcher is separate from ProcessMonitor — it works even for
    processes not spawned by S-IDE (e.g. a server you started in another
    terminal that imported monitor.instrument).
    """

    POLL_INTERVAL = 1.5   # seconds between file reads

    def __init__(self, project_root: str) -> None:
        self._path        = os.path.join(
            os.path.abspath(project_root), ".side-metrics.json"
        )
        self._data:  dict = {}   # latest parsed content
        self._lock        = threading.Lock()
        self._mtime: float = 0.0
        self._thread: threading.Thread | None = None
        self._stop_event  = threading.Event()

    def start(self) -> None:
        """Start background polling thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="metrics-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.wait(timeout=self.POLL_INTERVAL):
            self._poll()

    def _poll(self) -> None:
        """Read metrics file if it has changed since last read."""
        try:
            mtime = os.path.getmtime(self._path)
            if mtime <= self._mtime:
                return
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._data  = data
                self._mtime = mtime
        except (OSError, json.JSONDecodeError):
            pass

    def get_file_metrics(self) -> dict:
        """
        Return per-file timing data keyed by relative path.
        {
          "src/parser.py": {
            "calls": 42, "total_ms": 1234.5,
            "avg_ms": 29.4, "max_ms": 201.0,
            "last_ms": 28.1, "last_ts": 1700000000.0
          }, ...
        }
        """
        with self._lock:
            return dict(self._data.get("files", {}))

    def get_function_metrics(self) -> dict:
        """Return per-function timing data keyed by 'rel_path::func_name'."""
        with self._lock:
            return dict(self._data.get("functions", {}))

    def get_pid(self) -> int | None:
        with self._lock:
            return self._data.get("pid")

    def get_updated(self) -> float:
        with self._lock:
            return self._data.get("updated", 0.0)

    def is_active(self) -> bool:
        """True if metrics file was updated within the last 10 seconds."""
        return (time.time() - self.get_updated()) < 10.0

    @property
    def metrics_path(self) -> str:
        return self._path


import json  # needed for _poll

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
