# process/

Subprocess lifecycle management for projects running inside S-IDE.

## process_manager.py

`ProcessManager` — registry of all active processes for an IDE session.
`ManagedProcess` — wraps a single subprocess.

### Features

- **Start**: `mgr.start(name, command, cwd)` — spawns via `subprocess.Popen`, returns a `ManagedProcess`
- **Stop**: SIGTERM → 3s grace → SIGKILL
- **Suspend / Resume**: SIGSTOP / SIGCONT on POSIX; Windows thread suspend via ctypes
- **Log buffer**: last 500 lines of stdout+stderr, per-process ring buffer
- **Callbacks**: `proc.on_stdout(cb)`, `proc.on_stderr(cb)`, `proc.on_exit(cb)` — called from reader threads, keep them non-blocking
- **Pipe cleanup**: reader threads close stdout/stderr on process exit to avoid ResourceWarning

### Thread model

Each process gets three daemon threads: stdout reader, stderr reader, waiter.
All state mutations are protected by `self._lock`.

### Usage

```python
from process.process_manager import ProcessManager

mgr  = ProcessManager()
proc = mgr.start(name="dev", command="python main.py", cwd="/my/project")
proc.on_stdout(lambda line: print("OUT:", line))

mgr.suspend(proc.id)
mgr.resume(proc.id)
mgr.stop(proc.id)
mgr.stop_all()   # call on IDE shutdown
```
