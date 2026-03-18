"""
build/sandbox.py
================
Runs a project script in a disposable temporary directory.

Two sandbox modes
-----------------
  "clean"    — copy the project, run cleaner (cache/logs tiers), then launch
  "minified" — copy the project, clean it, minify it, then launch

Lifecycle
---------
1. `SandboxRun.prepare()` — creates the temp dir and applies transforms
2. `SandboxRun.start(command)`  — launches the process in the temp dir
3. `SandboxRun.stop()` — stops the process (SIGTERM → SIGKILL)
4. `SandboxRun.cleanup()` — deletes the temp dir, retains logs

Log retention
-------------
Before the temp dir is deleted, all *.log files and logs/ subdirectories
are copied to `<project_root>/logs/sandbox/<timestamp>/`.  This directory
persists across runs so you can compare logs from different sandbox runs.
Only the PREVIOUS run's log dir is kept by default (configurable).

Usage
-----
    from build.sandbox import SandboxRun, SandboxOptions

    opts = SandboxOptions(mode="minified", keep_log_runs=3)
    run  = SandboxRun("/my/project", opts)
    run.prepare()
    run.start("python main.py --port 8080")

    # ... later ...
    run.stop()
    log_dir = run.cleanup()   # returns path to retained logs
    print("Logs saved to:", log_dir)
"""

from __future__ import annotations
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


@dataclass
class SandboxOptions:
    """Options for a sandbox run."""
    mode:           str       = "clean"     # "clean" | "minified"
    keep_log_runs:  int       = 3           # number of log dirs to retain
    clean_tiers:    list[str] = field(default_factory=lambda: ["cache", "logs"])
    minify_opts:    dict      = field(default_factory=dict)
    # Dirs/files to exclude from the temp copy entirely
    exclude:        list[str] = field(default_factory=lambda: [
        "versions", ".git", "__pycache__", ".venv", "venv", "dist", "build",
    ])


class SandboxRun:
    """
    Manages a single sandbox execution — prepare, start, stop, cleanup.
    Thread-safe: stop() and cleanup() may be called from any thread.
    """

    def __init__(self, project_root: str, opts: SandboxOptions | None = None):
        self.project_root = os.path.abspath(project_root)
        self.opts         = opts or SandboxOptions()
        self._tmp_dir:    str | None  = None
        self._proc                    = None   # ManagedProcess
        self._log_dir:    str | None  = None   # retained log path after cleanup
        self._prepared:   bool        = False
        self._stdout_cb:  list[Callable] = []
        self._stderr_cb:  list[Callable] = []
        self._exit_cb:    list[Callable] = []

    # ── Setup ─────────────────────────────────────────────────────────────────

    def prepare(self) -> str:
        """
        Create the temp dir and apply the selected transforms.
        Returns the temp dir path.
        """
        self._tmp_dir = tempfile.mkdtemp(prefix="s-ide-run-")

        # 1. Copy source
        self._copy_source()

        # 2. Clean
        try:
            from build.cleaner import clean_project, CleanOptions
            clean_project(self._tmp_dir, CleanOptions(
                tiers=self.opts.clean_tiers, verbose=False
            ))
        except Exception:
            pass

        # 3. Minify (if requested)
        if self.opts.mode == "minified":
            try:
                from build.minifier import minify_project, MinifyOptions
                min_stage = tempfile.mkdtemp(prefix="s-ide-min-")
                opts = MinifyOptions(**self.opts.minify_opts) if self.opts.minify_opts \
                       else MinifyOptions(strip_docstrings=True, strip_comments=True)
                minify_project(self._tmp_dir, min_stage, opts)
                shutil.rmtree(self._tmp_dir)
                self._tmp_dir = min_stage
            except Exception as e:
                pass  # fall back to clean-only

        self._prepared = True
        return self._tmp_dir

    def _copy_source(self) -> None:
        """Copy project to temp dir, respecting exclusions."""
        exclude = set(self.opts.exclude)
        for item in os.listdir(self.project_root):
            if item in exclude or item.startswith("."):
                continue
            src = os.path.join(self.project_root, item)
            dst = os.path.join(self._tmp_dir, item)
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst,
                                    ignore=shutil.ignore_patterns(
                                        "__pycache__", "*.pyc", "*.pyo"))
                else:
                    shutil.copy2(src, dst)
            except Exception:
                pass

    # ── Execution ─────────────────────────────────────────────────────────────

    def on_stdout(self, cb: Callable[[str], None]) -> None:
        self._stdout_cb.append(cb)

    def on_stderr(self, cb: Callable[[str], None]) -> None:
        self._stderr_cb.append(cb)

    def on_exit(self, cb: Callable[[int], None]) -> None:
        self._exit_cb.append(cb)

    def start(self, command: str, name: str = "") -> object:
        """
        Launch command inside the temp dir.
        Returns the ManagedProcess.  Raises RuntimeError if not prepared.
        """
        if not self._prepared or not self._tmp_dir:
            raise RuntimeError("call prepare() before start()")

        from process.process_manager import ProcessManager
        mgr  = ProcessManager()
        proc = mgr.start(
            name=name or f"sandbox:{os.path.basename(self.project_root)}",
            command=command,
            cwd=self._tmp_dir,
        )
        self._proc = proc

        for cb in self._stdout_cb:
            proc.on_stdout(cb)
        for cb in self._stderr_cb:
            proc.on_stderr(cb)
        for cb in self._exit_cb:
            proc.on_exit(cb)

        return proc

    def stop(self) -> None:
        """Stop the running process (SIGTERM → SIGKILL after 3s)."""
        if self._proc:
            try:
                self._proc.stop()
            except Exception:
                pass

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> str:
        """
        Stop the process, retain logs, delete the temp dir.
        Returns the path to the retained log directory.
        """
        self.stop()

        log_dir = ""
        if self._tmp_dir and os.path.isdir(self._tmp_dir):
            log_dir = self._retain_logs()
            try:
                shutil.rmtree(self._tmp_dir, ignore_errors=True)
            except Exception:
                pass
            self._tmp_dir = None

        self._log_dir = log_dir
        return log_dir

    def _retain_logs(self) -> str:
        """
        Copy log files from temp dir to
        <project_root>/logs/sandbox/<timestamp>/.
        Prune old runs beyond keep_log_runs.
        Returns the path of the new log directory.
        """
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        sandbox_logs_root = os.path.join(self.project_root, "logs", "sandbox")
        os.makedirs(sandbox_logs_root, exist_ok=True)
        log_dir = os.path.join(sandbox_logs_root, ts)
        os.makedirs(log_dir, exist_ok=True)

        # Collect log files from temp dir
        copied = 0
        for dirpath, _, filenames in os.walk(self._tmp_dir):
            rel = os.path.relpath(dirpath, self._tmp_dir)
            for fname in filenames:
                is_log = fname.endswith(".log") or "logs" in rel.split(os.sep)
                if is_log:
                    src = os.path.join(dirpath, fname)
                    dst_dir = os.path.join(log_dir, rel)
                    os.makedirs(dst_dir, exist_ok=True)
                    shutil.copy2(src, os.path.join(dst_dir, fname))
                    copied += 1

        # Also copy any .side-metrics.json for timing data
        metrics_src = os.path.join(self._tmp_dir, ".side-metrics.json")
        if os.path.isfile(metrics_src):
            shutil.copy2(metrics_src, os.path.join(log_dir, ".side-metrics.json"))
            copied += 1

        # Prune old log dirs beyond keep_log_runs
        if self.opts.keep_log_runs > 0:
            try:
                runs = sorted(
                    [d for d in os.listdir(sandbox_logs_root)
                     if os.path.isdir(os.path.join(sandbox_logs_root, d))],
                    reverse=True,
                )
                for old in runs[self.opts.keep_log_runs:]:
                    shutil.rmtree(os.path.join(sandbox_logs_root, old),
                                  ignore_errors=True)
            except Exception:
                pass

        return log_dir if copied > 0 else ""

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def tmp_dir(self) -> str | None:
        return self._tmp_dir

    @property
    def log_dir(self) -> str | None:
        """Path to the retained log directory after cleanup(), or None."""
        return self._log_dir

    @property
    def is_running(self) -> bool:
        return (self._proc is not None and
                self._proc.info().get("status") == "running")


def list_sandbox_logs(project_root: str) -> list[dict]:
    """
    Return metadata for all retained sandbox log directories.
    [{name, path, size, modified}] sorted newest-first.
    """
    logs_dir = os.path.join(os.path.abspath(project_root), "logs", "sandbox")
    if not os.path.isdir(logs_dir):
        return []
    results = []
    for entry in os.listdir(logs_dir):
        full = os.path.join(logs_dir, entry)
        if not os.path.isdir(full):
            continue
        size = sum(
            os.path.getsize(os.path.join(dp, f))
            for dp, _, fs in os.walk(full)
            for f in fs
        )
        results.append({
            "name":     entry,
            "path":     full,
            "size":     size,
            "modified": os.path.getmtime(full),
        })
    return sorted(results, key=lambda x: x["modified"], reverse=True)
