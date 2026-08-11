# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
logsetup.py — shared logging setup for S-IDE
============================================
Writes S-IDE's log to <repo_root>/logs/s-ide.log, computed relative to this
module's own __file__ — never a hardcoded home path. If the log file cannot
be opened, that is loud: a clear error is printed to stderr at startup,
never swallowed in a bare `except: pass`. Logging then falls back to stderr
so the app still runs with its diagnostics visible.

    from logsetup import setup_logging
    log = setup_logging()                      # shared "s-ide" logger
    log.info("some event ...")                 # from any module

Child loggers (`logging.getLogger("s-ide.server")`) propagate to the
configured "s-ide" logger, so every module logs to the same file with its
own name on the line.
"""

import logging
import os
import sys

_LOG_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE  = os.path.join(_LOG_DIR, "s-ide.log")

_configured = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the shared 's-ide' logger once and return it. Idempotent."""
    global _configured
    root_log = logging.getLogger("s-ide")
    if _configured:
        return root_log
    root_log.setLevel(level)
    for h in list(root_log.handlers):
        root_log.removeHandler(h)
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    except Exception as e:
        print(f"[s-ide] ERROR: cannot write log file {LOG_FILE}: {e}", file=sys.stderr)
        print("[s-ide] Logging to stderr instead.", file=sys.stderr)
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root_log.addHandler(handler)
    root_log.propagate = False
    _configured = True
    return root_log
