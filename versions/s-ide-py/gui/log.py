"""
gui/log.py
==========
S-IDE application logging.

Log file location
-----------------
Logs live in the project's own logs/ directory, not in ~/:

    ~/DevOps/s-ide-py/logs/s-ide.log

The path is computed relative to this file's location so it works
regardless of the working directory when you launch the app.

The path is printed to stderr on startup:
    [s-ide] log → /home/you/DevOps/s-ide-py/logs/s-ide.log

Tail it while the app runs:
    tail -f ~/DevOps/s-ide-py/logs/s-ide.log

Two outputs
-----------
1. Rotating file  — logs/s-ide.log (2 MB, 5 backups)
2. In-memory ring — last 1000 lines, read by the in-app LOG panel

Usage
-----
    from gui.log import get_logger, get_log_path, recent_lines, clear_ring

    log = get_logger(__name__)
    log.info("Parser started for %s", path)
    log.warning("README missing in src/")
    log.error("Parse failed: %s", exc)
"""

from __future__ import annotations
import logging
import logging.handlers
import os
import sys
from collections import deque

# ── Paths ─────────────────────────────────────────────────────────────────────
# gui/log.py lives at  <project_root>/gui/log.py
# logs/ lives at       <project_root>/logs/
_HERE     = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_HERE)
LOGS_DIR  = os.path.join(_ROOT_DIR, "logs")
LOG_PATH  = os.path.join(LOGS_DIR, "s-ide.log")
MAX_RING  = 1000

# ── Ring buffer handler ───────────────────────────────────────────────────────
_ring: deque = deque(maxlen=MAX_RING)

class _RingHandler(logging.Handler):
    """Appends formatted records to the in-memory ring buffer."""
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _ring.append((record.levelname, self.format(record)))
        except Exception:
            pass

# ── Logger setup (runs once on import) ───────────────────────────────────────
_root_logger = logging.getLogger("side")
_root_logger.setLevel(logging.DEBUG)

_fmt = logging.Formatter(
    fmt="%(asctime)s %(levelname)-8s %(name)-24s %(message)s",
    datefmt="%H:%M:%S",
)

# 1. File handler — create logs/ dir first
_file_ok = False
try:
    os.makedirs(LOGS_DIR, exist_ok=True)
    _fh = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    _fh.setFormatter(_fmt)
    _root_logger.addHandler(_fh)
    _file_ok = True
except OSError as _e:
    print(f"[s-ide] WARNING: cannot open log file {LOG_PATH}: {_e}", file=sys.stderr)

# 2. Ring buffer
_rh = _RingHandler()
_rh.setFormatter(_fmt)
_root_logger.addHandler(_rh)

# 3. Stderr — WARNING and above only (keeps terminal tidy)
_sh = logging.StreamHandler(sys.stderr)
_sh.setLevel(logging.WARNING)
_sh.setFormatter(_fmt)
_root_logger.addHandler(_sh)

# Announce — always visible in terminal
_loc = LOG_PATH if _file_ok else "(file unavailable)"
_root_logger.info("session started — log → %s", _loc)
print(f"[s-ide] log → {_loc}", file=sys.stderr, flush=True)


# ── Public API ────────────────────────────────────────────────────────────────

def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the 'side' namespace."""
    return logging.getLogger(f"side.{name}")

def get_log_path() -> str:
    """Return the absolute path of the log file."""
    return LOG_PATH

def get_logs_dir() -> str:
    """Return the directory containing log files."""
    return LOGS_DIR

def recent_lines(n: int = MAX_RING) -> list[tuple[str, str]]:
    """Return up to n recent (level, formatted_message) tuples, oldest first."""
    items = list(_ring)
    return items[-n:]

def clear_ring() -> None:
    """Clear the in-memory log ring buffer."""
    _ring.clear()
