"""
process/process_manager.py
==========================
Manages child processes spawned from the IDE.

Each process is wrapped in a ManagedProcess instance that:
  - Captures stdout/stderr into a ring buffer (last 500 lines)
  - Emits lines to registered callbacks in real time
  - Supports stop (SIGTERM → SIGKILL), suspend (SIGSTOP), resume (SIGCONT)
  - Tracks status: 'running' | 'stopped' | 'suspended' | 'crashed'

ProcessManager is the top-level registry. One instance should live for
the lifetime of the IDE session.

Platform note
-------------
SIGSTOP / SIGCONT (suspend/resume) work on Linux and macOS.
On Windows, suspend falls back to a best-effort TerminateProcess approach
since POSIX signals are not available. Detected via sys.platform.

Usage
-----
    mgr = ProcessManager()
    proc = mgr.start(name="dev server", command="python main.py", cwd="/my/project")
    proc.on_stdout(lambda line: print("OUT:", line))

    mgr.suspend(proc.id)
    mgr.resume(proc.id)
    mgr.stop(proc.id)

    all_procs = mgr.list()          # list of info dicts
    logs = mgr.logs(proc.id)        # list of {stream, line, ts} dicts
"""

from __future__ import annotations
import os
import sys
import signal
import shlex
import threading
import secrets
from collections import deque
from datetime import datetime, timezone
from subprocess import Popen, PIPE
from typing import Callable


# ── Status constants ──────────────────────────────────────────────────────────
STATUS_RUNNING   = "running"
STATUS_STOPPED   = "stopped"
STATUS_SUSPENDED = "suspended"
STATUS_CRASHED   = "crashed"

# ── Ring-buffer size ──────────────────────────────────────────────────────────
MAX_LOG_LINES = 500


class ManagedProcess:
    """
    Wraps a single subprocess with status tracking and log buffering.

    Callbacks registered via on_stdout / on_stderr / on_exit are called
    from a background reader thread — keep them non-blocking.
    """

    def __init__(self, proc_id: str, name: str, command: str, cwd: str | None):
        self.id         = proc_id
        self.name       = name
        self.command    = command
        self.cwd        = cwd or os.getcwd()
        self.status     = STATUS_RUNNING
        self.started_at = datetime.now(tz=timezone.utc).isoformat()
        self.exit_code: int | None = None
        self.pid: int | None = None

        self._proc: Popen | None = None
        self._log: deque = deque(maxlen=MAX_LOG_LINES)
        self._lock = threading.Lock()

        self._stdout_cbs: list[Callable[[str], None]] = []
        self._stderr_cbs: list[Callable[[str], None]] = []
        self._exit_cbs:   list[Callable[[int], None]] = []

    # ── Callback registration ─────────────────────────────────────────────────

    def on_stdout(self, cb: Callable[[str], None]) -> None:
        """Register callback for stdout lines. Called from reader thread."""
        self._stdout_cbs.append(cb)

    def on_stderr(self, cb: Callable[[str], None]) -> None:
        """Register callback for stderr lines. Called from reader thread."""
        self._stderr_cbs.append(cb)

    def on_exit(self, cb: Callable[[int], None]) -> None:
        """Register callback for process exit. Called with exit code."""
        self._exit_cbs.append(cb)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _push_line(self, stream: str, line: str) -> None:
        entry = {"stream": stream, "line": line, "ts": datetime.now(tz=timezone.utc).isoformat()}
        with self._lock:
            self._log.append(entry)
        cbs = self._stdout_cbs if stream == "stdout" else self._stderr_cbs
        for cb in cbs:
            try:
                cb(line)
            except Exception:
                pass

    def _reader(self, stream, stream_name: str) -> None:
        """Background thread: read lines from a stream and push to buffer."""
        try:
            for raw in stream:
                line = raw.rstrip("\n\r") if isinstance(raw, str) else raw.decode("utf-8", errors="replace").rstrip("\n\r")
                if line:
                    self._push_line(stream_name, line)
        except Exception:
            pass

    def _waiter(self) -> None:
        """Background thread: wait for process exit, update status, close pipes."""
        code = self._proc.wait()
        self.exit_code = code
        with self._lock:
            if self.status not in (STATUS_STOPPED, STATUS_SUSPENDED):
                self.status = STATUS_STOPPED if code == 0 else STATUS_CRASHED
        # Close pipes explicitly to suppress ResourceWarning from GC
        for stream in (self._proc.stdout, self._proc.stderr, self._proc.stdin):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        for cb in self._exit_cbs:
            try:
                cb(code)
            except Exception:
                pass

    # ── Spawn ─────────────────────────────────────────────────────────────────

    def spawn(self) -> "ManagedProcess":
        """Start the subprocess and launch reader/waiter threads."""
        try:
            args = shlex.split(self.command)
        except ValueError:
            args = self.command.split()

        self._proc = Popen(
            args,
            stdout=PIPE,
            stderr=PIPE,
            cwd=self.cwd,
            env=os.environ.copy(),
            text=True,
            bufsize=1,          # line-buffered
        )
        self.pid = self._proc.pid

        # One thread per stream + one waiter
        for target, name in [
            (lambda: self._reader(self._proc.stdout, "stdout"), "stdout"),
            (lambda: self._reader(self._proc.stderr, "stderr"), "stderr"),
            (self._waiter, "waiter"),
        ]:
            t = threading.Thread(target=target, name=f"proc-{self.id}-{name}", daemon=True)
            t.start()

        return self

    # ── Control ───────────────────────────────────────────────────────────────

    def stop(self) -> bool:
        """Send SIGTERM; escalate to SIGKILL after 3 seconds."""
        if not self._proc or self.status in (STATUS_STOPPED, STATUS_CRASHED):
            return False
        try:
            self._proc.terminate()
            # Schedule SIGKILL in a daemon thread
            def _kill_after_timeout():
                import time
                time.sleep(3)
                try:
                    if self._proc.poll() is None:
                        self._proc.kill()
                except Exception:
                    pass
            t = threading.Thread(target=_kill_after_timeout, daemon=True)
            t.start()
            with self._lock:
                self.status = STATUS_STOPPED
            return True
        except Exception:
            return False

    def suspend(self) -> bool:
        """
        Pause the process.
        POSIX: SIGSTOP. Windows: suspend all threads (best effort).
        """
        if not self._proc or self.status != STATUS_RUNNING:
            return False
        try:
            if sys.platform == "win32":
                return self._win_suspend()
            os.kill(self.pid, signal.SIGSTOP)
            with self._lock:
                self.status = STATUS_SUSPENDED
            return True
        except Exception:
            return False

    def resume(self) -> bool:
        """
        Resume a suspended process.
        POSIX: SIGCONT. Windows: resume all threads.
        """
        if not self._proc or self.status != STATUS_SUSPENDED:
            return False
        try:
            if sys.platform == "win32":
                return self._win_resume()
            os.kill(self.pid, signal.SIGCONT)
            with self._lock:
                self.status = STATUS_RUNNING
            return True
        except Exception:
            return False

    def _win_suspend(self) -> bool:
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            h = kernel.OpenProcess(0x1F0FFF, False, self.pid)
            kernel.SuspendThread(h)
            kernel.CloseHandle(h)
            with self._lock:
                self.status = STATUS_SUSPENDED
            return True
        except Exception:
            return False

    def _win_resume(self) -> bool:
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            h = kernel.OpenProcess(0x1F0FFF, False, self.pid)
            kernel.ResumeThread(h)
            kernel.CloseHandle(h)
            with self._lock:
                self.status = STATUS_RUNNING
            return True
        except Exception:
            return False

    # ── Info / logs ───────────────────────────────────────────────────────────

    def info(self) -> dict:
        """Return a JSON-safe dict snapshot of this process's current state."""
        with self._lock:
            return {
                "id":        self.id,
                "name":      self.name,
                "command":   self.command,
                "cwd":       self.cwd,
                "status":    self.status,
                "pid":       self.pid,
                "startedAt": self.started_at,
                "exitCode":  self.exit_code,
                "logLines":  len(self._log),
            }

    def logs(self) -> list[dict]:
        """Return the ring-buffer log entries as a list of dicts."""
        with self._lock:
            return list(self._log)


# ── ProcessManager ────────────────────────────────────────────────────────────

class ProcessManager:
    """
    Registry of all managed processes for an IDE session.
    Thread-safe: individual operations are protected by a lock.
    """

    def __init__(self):
        self._procs: dict[str, ManagedProcess] = {}
        self._lock = threading.Lock()

    def start(self, name: str, command: str, cwd: str | None = None) -> ManagedProcess:
        """Spawn a new process and register it. Returns the ManagedProcess."""
        proc_id = secrets.token_hex(4)
        proc = ManagedProcess(proc_id=proc_id, name=name, command=command, cwd=cwd)
        proc.spawn()
        with self._lock:
            self._procs[proc_id] = proc
        return proc

    def get(self, proc_id: str) -> ManagedProcess | None:
        """Return the ManagedProcess with the given id, or None."""
        return self._procs.get(proc_id)

    def stop(self, proc_id: str) -> bool:
        proc = self._procs.get(proc_id)
        return proc.stop() if proc else False

    def suspend(self, proc_id: str) -> bool:
        proc = self._procs.get(proc_id)
        return proc.suspend() if proc else False

    def resume(self, proc_id: str) -> bool:
        proc = self._procs.get(proc_id)
        return proc.resume() if proc else False

    def stop_all(self) -> None:
        """Gracefully stop every running process. Called on IDE shutdown."""
        with self._lock:
            procs = list(self._procs.values())
        for proc in procs:
            proc.stop()

    def list(self) -> list[dict]:
        """Return info dicts for all registered processes."""
        with self._lock:
            return [p.info() for p in self._procs.values()]

    def logs(self, proc_id: str) -> list[dict] | None:
        """Return log lines for a process id, or None if not found."""
        proc = self._procs.get(proc_id)
        return proc.logs() if proc else None

    def purge_stopped(self) -> int:
        """Remove stopped/crashed processes from registry. Returns count removed."""
        with self._lock:
            dead = [pid for pid, p in self._procs.items()
                    if p.status in (STATUS_STOPPED, STATUS_CRASHED)]
            for pid in dead:
                del self._procs[pid]
        return len(dead)
