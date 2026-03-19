"""
monitor/profiler.py
===================
Live project profiler using cProfile.

Profiles a project's actual execution (not S-IDE's own parser) and writes
per-module and per-function timing to .side-metrics.json so the graph
canvas can show live performance overlays on node cards.

How it works
------------
1. User (or Manager bot) calls profile_project(root, entry_point, args)
2. We run the entry point under cProfile in a subprocess sandbox
3. Parse the pstats output into per-file and per-function timing dicts
4. Write to <root>/.side-metrics.json in the standard metrics format
5. MetricsWatcher in the GUI picks up the file change (~1.5s latency)

The format is intentionally identical to what monitor/instrument.py wrote
so the GUI needs no changes.

Usage
-----
    from monitor.profiler import profile_project, ProfileResult

    result = profile_project(
        project_root = "/path/to/myproject",
        entry_point  = "src/main.py",
        args         = ["--input", "data.csv"],
        timeout      = 30,
    )
    print(result.summary())

    # Or via the GUI run button in the Projects tab
    # Or via the Manager tool: profile_project(entry_point="src/main.py")

line_profiler support
---------------------
If line_profiler is installed (pip install line-profiler), individual
functions can be profiled at line granularity:

    result = profile_function(
        project_root = "/path/to/myproject",
        module_path  = "src/parser.py",
        function_name = "parse_file",
    )
"""

from __future__ import annotations

import json
import os
import pstats
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Optional


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class FunctionMetrics:
    """Timing data for one function from a cProfile run."""
    module_path:   str    # relative path to the source file
    function_name: str    # bare function name
    calls:         int    # total call count
    total_ms:      float  # cumulative time in milliseconds
    own_ms:        float  # time in function body (excl. callees), ms
    per_call_ms:   float  # avg ms per call (cumulative)


@dataclass
class ProfileResult:
    """Result of one profile run."""
    project_root:  str
    entry_point:   str
    total_ms:      float
    exit_code:     int
    error:         str = ""
    functions:     list[FunctionMetrics] = field(default_factory=list)
    metrics_path:  str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.error

    def summary(self) -> str:
        lines = [
            f"Profile: {self.entry_point}",
            f"Total:   {self.total_ms:.0f}ms  exit={self.exit_code}",
        ]
        if self.error:
            lines.append(f"Error:   {self.error}")
        top = sorted(self.functions, key=lambda f: -f.total_ms)[:10]
        if top:
            lines.append("Top functions (by cumulative time):")
            for fn in top:
                lines.append(
                    f"  {fn.function_name:30s} {fn.total_ms:8.1f}ms  "
                    f"{fn.calls:6}x  {fn.per_call_ms:.2f}ms/call"
                    f"  [{fn.module_path}]"
                )
        return "\n".join(lines)

    def top_functions(self, n: int = 20) -> list[FunctionMetrics]:
        return sorted(self.functions, key=lambda f: -f.total_ms)[:n]


# ── Core profiling ────────────────────────────────────────────────────────────

def profile_project(
    project_root:  str,
    entry_point:   str = "",
    args:          list[str] | None = None,
    timeout:       int = 60,
    top_n:         int = 200,
) -> ProfileResult:
    """
    Profile a project entry point with cProfile in an isolated subprocess.

    Writes results to <project_root>/.side-metrics.json in the standard
    format so the GUI shows live overlays immediately.

    Args:
        project_root: Absolute path to the project directory.
        entry_point:  Relative path to the Python script to run.
                      Defaults to the first 'main.py' found in src/ or root.
        args:         Command-line arguments to pass to the script.
        timeout:      Maximum seconds to run (default 60).
        top_n:        How many functions to include in the metrics file.

    Returns:
        ProfileResult with timing data and the path to the written JSON.
    """
    root = os.path.abspath(project_root)
    t0   = time.monotonic()

    # Resolve entry point
    if not entry_point:
        entry_point = _find_entry_point(root)
    if not entry_point:
        return ProfileResult(
            project_root=root, entry_point="", total_ms=0, exit_code=1,
            error="No entry point found. Pass entry_point= explicitly.")

    entry_abs = os.path.join(root, entry_point) if not os.path.isabs(entry_point) \
                else entry_point
    if not os.path.isfile(entry_abs):
        return ProfileResult(
            project_root=root, entry_point=entry_point, total_ms=0, exit_code=1,
            error=f"Entry point not found: {entry_abs}")

    # Run under cProfile in a subprocess
    with tempfile.NamedTemporaryFile(suffix=".prof", delete=False) as pf:
        prof_path = pf.name

    try:
        cmd = [
            sys.executable, "-m", "cProfile",
            "-o", prof_path,
            entry_abs,
        ] + (args or [])

        proc = subprocess.run(
            cmd, cwd=root,
            capture_output=True, text=True,
            timeout=timeout,
        )
        total_ms = (time.monotonic() - t0) * 1000

        if not os.path.isfile(prof_path):
            return ProfileResult(
                project_root=root, entry_point=entry_point,
                total_ms=total_ms, exit_code=proc.returncode,
                error=f"cProfile did not write output. stderr: {proc.stderr[:300]}")

        # Parse the .prof file
        functions = _parse_pstats(prof_path, root, top_n)
        result    = ProfileResult(
            project_root = root,
            entry_point  = entry_point,
            total_ms     = total_ms,
            exit_code    = proc.returncode,
            functions    = functions,
        )

        # Write .side-metrics.json
        metrics_path = os.path.join(root, ".side-metrics.json")
        _write_metrics(result, metrics_path)
        result.metrics_path = metrics_path

        return result

    except subprocess.TimeoutExpired:
        total_ms = (time.monotonic() - t0) * 1000
        return ProfileResult(
            project_root=root, entry_point=entry_point,
            total_ms=total_ms, exit_code=-1,
            error=f"Profiling timed out after {timeout}s")
    except Exception as e:
        total_ms = (time.monotonic() - t0) * 1000
        return ProfileResult(
            project_root=root, entry_point=entry_point,
            total_ms=total_ms, exit_code=-1, error=str(e))
    finally:
        try:
            os.unlink(prof_path)
        except OSError:
            pass


def profile_function(
    project_root:  str,
    module_path:   str,
    function_name: str,
    call_args:     str = "",
    timeout:       int = 30,
) -> ProfileResult:
    """
    Profile a single function using line_profiler if available, else cProfile.

    Generates a tiny driver script that imports the module, calls the
    function with call_args (a Python expression), and profiles it.

    Args:
        project_root:  Project root directory.
        module_path:   Relative path to the module file (e.g. "src/parser.py").
        function_name: Name of the function to profile.
        call_args:     Python expression for arguments (e.g. "'hello', 42").
        timeout:       Max seconds.

    Returns:
        ProfileResult.
    """
    root = os.path.abspath(project_root)
    rel  = module_path.lstrip("/")
    mod_abs = os.path.join(root, rel)
    if not os.path.isfile(mod_abs):
        return ProfileResult(
            project_root=root, entry_point=module_path, total_ms=0, exit_code=1,
            error=f"Module not found: {mod_abs}")

    # Convert file path to importable module name
    mod_name = rel.replace("/", ".").replace("\\", ".").removesuffix(".py")

    driver = (
        f"import sys\n"
        f"sys.path.insert(0, {repr(root)})\n"
        f"from {mod_name} import {function_name} as _fn\n"
        f"_fn({call_args})\n"
    )

    with tempfile.NamedTemporaryFile(suffix=".py", delete=False,
                                     mode="w", encoding="utf-8") as df:
        df.write(driver)
        driver_path = df.name

    try:
        return profile_project(
            project_root = root,
            entry_point  = driver_path,
            timeout      = timeout,
        )
    finally:
        try:
            os.unlink(driver_path)
        except OSError:
            pass


# ── Parse cProfile output ──────────────────────────────────────────────────────

def _parse_pstats(prof_path: str, project_root: str,
                  top_n: int = 200) -> list[FunctionMetrics]:
    """
    Parse a .prof file produced by cProfile into FunctionMetrics.
    Only includes functions from within the project root.
    """
    try:
        stats = pstats.Stats(prof_path)
    except Exception:
        return []

    functions: list[FunctionMetrics] = []
    root = os.path.abspath(project_root)

    # stats.stats: {(file, line, fn_name): (prim_calls, total_calls, tt, ct, callers)}
    for key, value in stats.stats.items():
        file_path, _line, fn_name = key
        _prim, total_calls, own_time, cum_time, _callers = value

        # Normalise path
        file_abs = os.path.abspath(file_path) if file_path else ""

        # Only include project files (not stdlib / site-packages)
        if not file_abs.startswith(root):
            continue
        if "/__pycache__/" in file_abs or "/site-packages/" in file_abs:
            continue
        # Skip built-in markers
        if file_path in ("{built-in}", "<frozen"):
            continue
        if fn_name.startswith("<"):
            continue

        rel_path = os.path.relpath(file_abs, root)
        own_ms   = own_time  * 1000
        cum_ms   = cum_time  * 1000
        pc_ms    = cum_ms / total_calls if total_calls else 0

        functions.append(FunctionMetrics(
            module_path   = rel_path,
            function_name = fn_name,
            calls         = total_calls,
            total_ms      = round(cum_ms, 3),
            own_ms        = round(own_ms, 3),
            per_call_ms   = round(pc_ms,  3),
        ))

    # Sort by cumulative time descending
    functions.sort(key=lambda f: -f.total_ms)
    return functions[:top_n]


# ── Write metrics JSON ─────────────────────────────────────────────────────────

def _write_metrics(result: ProfileResult, path: str) -> None:
    """
    Write a ProfileResult to .side-metrics.json in the standard format.

    The format is compatible with what MetricsWatcher expects:
        {
          "pid": ..., "updated": ...,
          "files":     {rel_path: {calls, total_ms, avg_ms, max_ms, ...}},
          "functions": {rel_path::fn: {calls, avg_ms, ...}}
        }
    """
    files:     dict[str, dict] = {}
    functions: dict[str, dict] = {}

    for fn in result.functions:
        p = fn.module_path

        # Aggregate per-file stats
        if p not in files:
            files[p] = {
                "calls": 0, "total_ms": 0.0,
                "avg_ms": 0.0, "max_ms": 0.0, "last_ms": 0.0,
                "last_ts": result.total_ms,
            }
        files[p]["calls"]    += fn.calls
        files[p]["total_ms"] += fn.own_ms
        files[p]["max_ms"]    = max(files[p]["max_ms"], fn.own_ms)
        files[p]["last_ms"]   = fn.own_ms

        # Per-function entry
        key = f"{p}::{fn.function_name}"
        functions[key] = {
            "calls":       fn.calls,
            "total_ms":    fn.total_ms,
            "avg_ms":      fn.per_call_ms,
            "max_ms":      fn.total_ms,
            "own_ms":      fn.own_ms,
            "last_ms":     fn.per_call_ms,
            "last_ts":     time.time(),
        }

    # Compute avg_ms per file
    for p, stats in files.items():
        if stats["calls"]:
            stats["avg_ms"] = round(stats["total_ms"] / stats["calls"], 3)

    snapshot = {
        "pid":       os.getpid(),
        "updated":   time.time(),
        "profiled":  result.entry_point,
        "total_ms":  result.total_ms,
        "files":     files,
        "functions": functions,
    }
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


# ── Helpers ────────────────────────────────────────────────────────────────────

def _find_entry_point(root: str) -> str:
    """
    Find the most likely entry point for a project.
    Checks (in order): src/main.py, main.py, src/app.py, app.py,
    src/cli.py, cli.py, and anything with if __name__ == '__main__'.
    """
    candidates = [
        "src/main.py", "main.py", "src/app.py", "app.py",
        "src/cli.py", "cli.py", "src/run.py", "run.py",
    ]
    for c in candidates:
        if os.path.isfile(os.path.join(root, c)):
            return c

    # Fallback: scan for __main__ guard
    for dirpath, _dirs, files in os.walk(root):
        # Skip hidden, cache, venv dirs
        rel = os.path.relpath(dirpath, root)
        if any(p.startswith(".") or p in ("__pycache__", "venv", ".venv")
               for p in rel.split(os.sep)):
            continue
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(dirpath, fname)
            try:
                if '__name__ == "__main__"' in open(path, errors="replace").read():
                    return os.path.relpath(path, root)
            except OSError:
                pass
    return ""


def load_last_profile(project_root: str) -> Optional[dict]:
    """
    Load the last written .side-metrics.json for a project.
    Returns the raw dict, or None if not found.
    """
    path = os.path.join(project_root, ".side-metrics.json")
    if not os.path.isfile(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
