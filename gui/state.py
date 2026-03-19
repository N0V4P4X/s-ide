"""
gui/state.py
============
Persistent session state for S-IDE.

Stores to ~/.s-ide-state.json on every meaningful change and reloads
on startup.  Survives restarts and updates (update.py preserves ~/.*)

State stored
------------
  projects        — list of {name, path} recently opened
  ai_history      — list of {role, content} messages per project
  terminal_history — list of command strings per project (last 200)
  viewport        — {x, y, z} last canvas position per project
  bottom_panel    — {height, tab} last bottom panel state
  editor_sessions — list of open file paths per project
"""

from __future__ import annotations
import json
import os
from typing import Any

_STATE_PATH = os.path.join(os.path.expanduser("~"), ".s-ide-state.json")
_DEFAULTS: dict = {
    "projects":         [],
    "ai_history":       {},   # project_root → [msg dicts]
    "terminal_history": {},   # project_root → [cmd strings]
    "viewport":         {},   # project_root → {x, y, z}
    "bottom_panel":     {"height": 260, "tab": "projects"},
    "editor_sessions":  {},   # project_root → [filepath strings]
}


def _load() -> dict:
    try:
        if os.path.isfile(_STATE_PATH):
            raw = json.load(open(_STATE_PATH, encoding="utf-8"))
            # Merge with defaults so new keys are always present
            merged = dict(_DEFAULTS)
            merged.update(raw)
            return merged
    except Exception:
        pass
    return dict(_DEFAULTS)


def _save(state: dict) -> None:
    try:
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, _STATE_PATH)
    except Exception:
        pass


class SessionState:
    """
    Read/write wrapper around the persisted state dict.
    Call save() after any mutation to flush to disk.
    """

    def __init__(self) -> None:
        self._data = _load()

    def save(self) -> None:
        _save(self._data)

    # ── Projects ──────────────────────────────────────────────────────────────

    @property
    def projects(self) -> list[dict]:
        return self._data.setdefault("projects", [])

    def add_project(self, name: str, path: str) -> None:
        path = os.path.abspath(path)
        self._data["projects"] = [
            p for p in self._data.get("projects", []) if p["path"] != path
        ]
        self._data["projects"].insert(0, {"name": name, "path": path})
        self._data["projects"] = self._data["projects"][:30]
        self.save()

    def remove_project(self, path: str) -> None:
        path = os.path.abspath(path)
        self._data["projects"] = [
            p for p in self._data.get("projects", []) if p["path"] != path
        ]
        self.save()

    # ── AI history ────────────────────────────────────────────────────────────

    def get_ai_history(self, project_root: str) -> list[dict]:
        key = os.path.abspath(project_root) if project_root else "__global__"
        return self._data.setdefault("ai_history", {}).get(key, [])

    def set_ai_history(self, project_root: str, messages: list) -> None:
        key = os.path.abspath(project_root) if project_root else "__global__"
        hist = self._data.setdefault("ai_history", {})
        # Store plain dicts; filter out system messages (rebuilt fresh each session)
        hist[key] = [
            {"role": m["role"] if isinstance(m, dict) else m.role,
             "content": m["content"] if isinstance(m, dict) else m.content}
            for m in messages
            if (m["role"] if isinstance(m, dict) else m.role) != "system"
        ][-100:]   # keep last 100 messages
        self.save()

    def clear_ai_history(self, project_root: str) -> None:
        key = os.path.abspath(project_root) if project_root else "__global__"
        self._data.setdefault("ai_history", {}).pop(key, None)
        self.save()

    # ── Terminal history ──────────────────────────────────────────────────────

    def get_terminal_history(self, project_root: str) -> list[str]:
        key = os.path.abspath(project_root) if project_root else "__global__"
        return self._data.setdefault("terminal_history", {}).get(key, [])

    def add_terminal_command(self, project_root: str, cmd: str) -> None:
        key = os.path.abspath(project_root) if project_root else "__global__"
        hist = self._data.setdefault("terminal_history", {}).setdefault(key, [])
        if cmd and (not hist or hist[-1] != cmd):
            hist.append(cmd)
            self._data["terminal_history"][key] = hist[-200:]
        self.save()

    # ── Viewport ──────────────────────────────────────────────────────────────

    def get_viewport(self, project_root: str) -> dict:
        key = os.path.abspath(project_root) if project_root else "__global__"
        return self._data.setdefault("viewport", {}).get(
            key, {"x": 0.0, "y": 0.0, "z": 1.0}
        )

    def set_viewport(self, project_root: str, x: float, y: float, z: float) -> None:
        key = os.path.abspath(project_root) if project_root else "__global__"
        self._data.setdefault("viewport", {})[key] = {"x": x, "y": y, "z": z}
        # Don't save on every pan/zoom — caller debounces

    def flush_viewport(self, project_root: str) -> None:
        """Call this on idle/close to actually write viewport to disk."""
        self.save()

    # ── Bottom panel ──────────────────────────────────────────────────────────

    @property
    def bottom_height(self) -> int:
        return self._data.get("bottom_panel", {}).get("height", 260)

    @bottom_height.setter
    def bottom_height(self, v: int) -> None:
        self._data.setdefault("bottom_panel", {})["height"] = max(80, min(v, 700))

    @property
    def bottom_tab(self) -> str:
        return self._data.get("bottom_panel", {}).get("tab", "projects")

    @bottom_tab.setter
    def bottom_tab(self, v: str) -> None:
        self._data.setdefault("bottom_panel", {})["tab"] = v
        self.save()

    # ── Editor sessions ───────────────────────────────────────────────────────

    def get_editor_sessions(self, project_root: str) -> list[str]:
        key = os.path.abspath(project_root) if project_root else "__global__"
        return self._data.setdefault("editor_sessions", {}).get(key, [])

    def set_editor_sessions(self, project_root: str, paths: list[str]) -> None:
        key = os.path.abspath(project_root) if project_root else "__global__"
        self._data.setdefault("editor_sessions", {})[key] = paths[:10]
        self.save()
