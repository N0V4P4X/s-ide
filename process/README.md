# process/

Subprocess lifecycle management.

## process_manager.py

```python
from process.process_manager import ProcessManager

mgr  = ProcessManager()
proc = mgr.start(name="dev", command="python main.py", cwd="/my/project")
proc.on_stdout(lambda line: print("OUT:", line))
proc.on_stderr(lambda line: print("ERR:", line))
proc.on_exit(lambda code: print("exited:", code))

mgr.suspend(proc.id)
mgr.resume(proc.id)
mgr.stop(proc.id)     # SIGTERM → 3s grace → SIGKILL
mgr.stop_all()        # call on IDE shutdown
```

Each process gets three daemon threads: stdout reader, stderr reader, waiter. All state protected by `self._lock`. Last 500 lines of output kept in a per-process ring buffer.

`proc.info()` returns `{"id", "name", "command", "status", "pid", "exit_code", "cpu_percent", "rss_mb"}`.
