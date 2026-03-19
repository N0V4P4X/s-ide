"""
gui/app.py
==========
S-IDE Desktop GUI — PyQt6-free, tkinter-based node graph editor.

Layout
------
  ┌─────────────────────────────────────────────────────┐
  │  TOPBAR  (logo · project · filters · search · zoom) │
  ├──────┬──────────────────────────────────┬────────────┤
  │SIDE  │  CANVAS  (infinite pan+zoom)     │ INSPECTOR  │
  │BAR   │  • node cards                    │ (slide in) │
  │      │  • bezier edges                  │            │
  │      │  • minimap                       │            │
  ├──────┴──────────────────────────────────┴────────────┤
  │  STATUSBAR (lang stats · parse time · proc badge)    │
  └─────────────────────────────────────────────────────-┘
  PROC PANEL — slides up from bottom-right

Runs the Python parser backend in-process (no server needed).
All GUI state is pure Python; no JS, no Electron.

Dependencies: tkinter (stdlib), nothing else for the GUI itself.
"""

from __future__ import annotations
import os
import sys

# ── Entrypoint Bootstrap ──────────────────────────────────────────────────────
# If this file is executed directly (python gui/app.py), we immediately
# re-import it as the `gui.app` module to prevent circular imports later caused
# by Python double-loading the file under `__main__` and `gui.app` namespaces.
if __name__ == "__main__":
    _ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _ROOT_DIR not in sys.path:
        sys.path.insert(0, _ROOT_DIR)
    import gui.app
    sys.exit(gui.app.main())

import re
import json
import subprocess
import math
import threading
import datetime
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox
from typing import Optional, Any, Union, List, Dict, Set, Tuple
from dataclasses import dataclass, field

# ── Path bootstrap ────────────────────────────────────────────────────────────
_GUI_DIR  = os.path.dirname(os.path.abspath(__file__))   # …/s-ide-py/gui
_ROOT_DIR = os.path.dirname(_GUI_DIR)                     # …/s-ide-py
for _p in (_ROOT_DIR, _GUI_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Colour palette ────────────────────────────────────────────────────────────
# Industrial phosphor-on-black: circuit board meets oscilloscope

P = {
    # Backgrounds — layered blacks
    "bg0":    "#060608",   # canvas void
    "bg1":    "#0c0c10",   # panel base
    "bg2":    "#111116",   # card surface
    "bg3":    "#18181f",   # raised element
    "bg4":    "#1f1f28",   # hover surface

    # Borders
    "line":   "#1c1c24",
    "line2":  "#262632",
    "line3":  "#32323f",

    # Text
    "t0":     "#e2e2f0",   # primary
    "t1":     "#9090a8",   # secondary
    "t2":     "#55556a",   # muted
    "t3":     "#2a2a38",   # ghost

    # Accents — phosphor green primary
    "green":  "#39ff8a",   # phosphor green (primary)
    "green2": "#1a7a42",   # dim green
    "blue":   "#4da6ff",
    "amber":  "#ffaa33",
    "red":    "#ff4455",
    "purple": "#aa66ff",
    "cyan":   "#33ddcc",
    "yellow": "#ddcc33",
    "pink":   "#ff55aa",

    # Grid lines
    "grid_minor": "#0e0e14",
    "grid_major": "#13131a",
}

# Category → (fill, border, accent, text)
CAT = {
    "python":     (P["bg2"], "#2a4a2a", P["green"],  P["t1"]),
    "javascript": (P["bg2"], "#2a3a1a", "#aadd44",   P["t1"]),
    "typescript": (P["bg2"], "#1a2a4a", P["blue"],   P["t1"]),
    "react":      (P["bg2"], "#2a1a4a", P["purple"], P["t1"]),
    "config":     (P["bg2"], "#3a3a1a", P["yellow"], P["t1"]),
    "docs":       (P["bg2"], "#1a3a3a", P["cyan"],   P["t1"]),
    "shell":      (P["bg2"], "#3a1a3a", P["pink"],   P["t1"]),
    "style":      (P["bg2"], "#1a3a2a", "#44ddaa",   P["t1"]),
    "markup":     (P["bg2"], "#3a2a1a", P["amber"],  P["t1"]),
    "database":   (P["bg2"], "#1a1a3a", "#6677ff",   P["t1"]),
    "go":         (P["bg2"], "#1a3a3a", "#44ccdd",   P["t1"]),
    "rust":       (P["bg2"], "#3a2a1a", "#ff8833",   P["t1"]),
    "other":      (P["bg2"], "#222230", P["t2"],     P["t2"]),
}

# Edge type → (colour, dash_pattern)
EDGE_STYLES = {
    "import":          (P["green"],  ()),
    "require":         (P["green"],  (6, 4)),
    "import-dynamic":  (P["amber"],  (4, 3)),
    "reexport":        (P["purple"], ()),
    "external":        (P["t3"],     (3, 6)),
    "npm-dep":         (P["yellow"], (2, 5)),
    "shell-source":    (P["pink"],   (6, 3)),
    "shell-call":      (P["pink"],   (3, 4)),
}
EDGE_DEFAULT = (P["t3"], ())

# Kind → display glyph
KIND_ICON = {
    "function":       "ƒ",
    "arrow-function": "→",
    "class":          "◈",
    "component":      "⬡",
    "method":         "·ƒ",
    "dunder":         "⟦⟧",
    "shell-function": "$",
    "npm-script":     "▶",
    "config-key":     "⚙",
}

# Node card geometry
NW          = 230    # node width (canvas units)
NH_HEADER   = 44     # header height
NH_TAG_ROW  = 24     # tag strip height (if tags present)
NH_DEF_ROW  = 20     # per definition row
NH_EXP_ROW  = 22     # exports strip height (if present)
NH_PAD      = 8      # bottom padding
MAX_DEFS    = 6      # max definition rows shown


def cat_style(category: str):
    """Return (fill, border, accent, text) colours for a file category."""
    return CAT.get(category, CAT["other"])


def edge_style(etype: str):
    """Return (colour, dash_pattern) for an edge type."""
    return EDGE_STYLES.get(etype, EDGE_DEFAULT)


def node_height(node: dict | None) -> int:
    if not node:
        return NH_HEADER + NH_PAD
    """Calculate the canvas height of a node card in world units."""
    h = NH_HEADER + NH_PAD
    if node.get("tags"):
        h += NH_TAG_ROW
    n_defs = min(len(node.get("definitions") or []), MAX_DEFS)
    if n_defs:
        h += 16 + n_defs * NH_DEF_ROW   # section label + rows
    if node.get("exports"):
        h += NH_EXP_ROW
    return h


def fmt_size(b: int) -> str:
    """Format a byte count as a human-readable string."""
    if not b:
        return "0B"
    if b < 1024:
        return f"{b}B"
    if b < 1024 * 1024:
        return f"{b/1024:.1f}KB"
    return f"{b/1024/1024:.1f}MB"



# Now safe to import anything from the s-ide-py package tree
# Import workarounds to help some IDEs resolve local packages while maintaining script compatibility.
try:
    from .log import get_logger, get_log_path, recent_lines, clear_ring
    from .editor import EditorWindow
    from .teams_canvas import TeamsCanvasMixin
    from .canvas_mixin import CanvasMixin
    from .inspector_mixin import InspectorMixin
    from .dialogs_mixin import DialogsMixin
    from .ai_mixin import AIMixin
except (ImportError, ValueError):
    from gui.log import get_logger, get_log_path, recent_lines, clear_ring
    from gui.editor import EditorWindow
    from gui.teams_canvas import TeamsCanvasMixin
    from gui.canvas_mixin import CanvasMixin
    from gui.inspector_mixin import InspectorMixin
    from gui.dialogs_mixin import DialogsMixin
    from gui.ai_mixin import AIMixin

try:
    from ..monitor.perf import MetricsWatcher, ParseTimer, ProcessMonitor
    from ..monitor.instrumenter import rollback_available, rollback, Instrumenter, InstrumentOptions
    from ..ai.client import OllamaClient, ChatMessage as CM
    from ..ai.tools import TOOLS, dispatch_tool
    from ..ai.context import build_context, build_system_message
    from ..ai.manager import Manager, scaffold_new_project
    from ..process.process_manager import ProcessManager
    from ..build.sandbox import SandboxRun, SandboxOptions
    from ..parser.project_parser import parse_project
    from ..version.version_manager import (
        archive_version, apply_update, list_versions, compress_loose as compress_versions
    )
    from ..build.packager import package_project, PackageOptions
    from ..parser.project_config import load_project_config, save_project_config, bump_version
    from ..build.cleaner import clean_project, CleanOptions
except (ImportError, ValueError):
    from monitor.perf import MetricsWatcher, ParseTimer, ProcessMonitor
    from monitor.instrumenter import rollback_available, rollback, Instrumenter, InstrumentOptions
    from ai.client import OllamaClient, ChatMessage as CM
    from ai.tools import TOOLS, dispatch_tool
    from ai.context import build_context, build_system_message
    from ai.manager import Manager, scaffold_new_project
    from process.process_manager import ProcessManager
    from build.sandbox import SandboxRun, SandboxOptions
    from parser.project_parser import parse_project
    from version.version_manager import (
        archive_version, apply_update, list_versions, compress_loose as compress_versions
    )
    from build.packager import package_project, PackageOptions
    from parser.project_config import load_project_config, save_project_config, bump_version
    from build.cleaner import clean_project, CleanOptions



# ── Application ───────────────────────────────────────────────────────────────

class SIDE_App(tk.Tk, TeamsCanvasMixin, CanvasMixin, InspectorMixin, DialogsMixin, AIMixin):
    """Root window. Owns all top-level state."""

    def __init__(self):
        super().__init__()
        self.title("S-IDE")
        self._log = get_logger("app")
        self._log.info("S-IDE starting")
        self.configure(bg=P["bg0"])
        self.geometry("1440x900")
        self.minsize(900, 600)

        # ── Fonts (fallback chain for portability) ────────────────────────────
        mono_families = ["IBM Plex Mono", "JetBrains Mono", "Fira Code",
                         "Cascadia Code", "Consolas", "Courier New"]
        self._mono = self._best_font(mono_families, 11)
        self._mono_m  = self._best_font(mono_families, 10)
        self._mono_s  = self._best_font(mono_families, 9)
        self._mono_xs = self._best_font(mono_families, 8)
        self._mono_l  = self._best_font(mono_families, 12, bold=True)

        # ── State ─────────────────────────────────────────────────────────────
        self.graph: Optional[dict] = None           # current ProjectGraph dict
        self.positions: dict       = {}             # node_id → (x, y)
        self.projects:  List[Dict[str, Any]] = []             # [{name, path}]
        self.processes: dict[str, Any] = {}         # proc_id → ManagedProcess

        # Viewport
        self.vp_x = 0.0
        self.vp_y = 0.0
        self.vp_z = 1.0

        # Selection / hover
        self.sel_nodes: set  = set()
        self.sel_edges: set  = set()
        self.hov_node: Optional[str] = None
        self.hov_edge: Optional[str] = None

        # Filters
        self.show_ext    = False   # external deps hidden by default
        self.filter_cat  = ""      # legacy compat
        self.filter_cats: set = set()           # multi-select active cats
        self.hidden_cats: set = {"docs", "config"}  # hidden unless selected
        self.search_q    = ""

        # Drag/pan state
        self._drag: Optional[dict] = None   # {id, ox, oy, sx, sy}
        self._pan:  Optional[dict] = None   # {sx, sy, ox, oy}

        # AI panel state
        self._ai_model: str = "llama3.2"
        self._manager: Optional[Manager] = None        # ai.manager.Manager
        self._bake_running: bool = False
        self._bake_deadline: float = 0.0
        self._changed_nodes: set = set()    # recently written node ids
        self._ai_input_var  = tk.StringVar()
        self._ai_status_var = tk.StringVar()
        self._ai_model_var  = tk.StringVar(value="llama3.2")
        self._ai_messages: list[CM] = []
        self._ai_available: bool = False
        self._ai_state: dict = {
            'in_thought': False, 'in_code': False, 'in_bold': False, 'buffer': ""
        }
        self._ai_conv: Optional[tk.Text] = None
        self._ai_input: Optional[tk.Entry] = None

        # Plan / Playground
        self._plan_text: Optional[tk.Text] = None
        self._play_text: Optional[tk.Text] = None
        self._play_out: Optional[tk.Text] = None

        # Terminal
        self._term_tab: Optional[tk.Frame] = None
        self._term_out: Optional[tk.Text] = None
        self._term_input: Optional[tk.Entry] = None
        self._term_input_var = tk.StringVar()
        self._term_history: List[str] = []
        self._term_history_idx: int = -1
        self._term_current_draft: str = ""

        # Bottom Panel & Tabs
        self._main_pw: Optional[tk.PanedWindow] = None
        self._main_paned: Optional[tk.PanedWindow] = None
        self._bottom_wrapper: Optional[tk.Frame] = None
        self._bottom_body: Optional[tk.Frame] = None
        self._bottom_expanded: bool = True
        self._bottom_notebook: Optional[ttk.Notebook] = None
        self._saved_bottom_height: int = 250
        self._tabs: dict[str, dict] = {}
        self._current_tab: Optional[str] = None

        # Sidebar & Panels
        self._sidebar: Optional[tk.Frame] = None
        self._proj_tab: Optional[tk.Frame] = None
        self._plan_tab: Optional[tk.Frame] = None
        self._play_tab: Optional[tk.Frame] = None
        self._editor_tab: Optional[tk.Frame] = None
        self._ai_tab: Optional[tk.Frame] = None
        self._terminal_tab: Optional[tk.Frame] = None
        self._proj_list_frame: Optional[tk.Frame] = None
        
        self._inspector_width: int = 300

        # Canvas & Inspector
        self._canvas: Optional[tk.Canvas] = None
        self._minimap: Optional[tk.Canvas] = None
        self._inspector: Optional[tk.Frame] = None
        self._inspector_open: bool = False
        self._insp_inner: Optional[tk.Frame] = None
        self._zw_pct: Optional[tk.Label] = None

        # UI elements (buttons/vars)
        self._lbl_project: Optional[tk.Label] = None
        self._doc_badge: Optional[tk.Label] = None
        self._doc_badge_var = tk.StringVar(value="")
        self._cat_btns: dict[str, tk.Label] = {}
        self._ext_btn: Optional[tk.Label] = None
        self._zoom_var = tk.StringVar(value="100%")
        self._search_var = tk.StringVar()
        self._ai_btn: Optional[tk.Label] = None
        self._metrics_btn: Optional[tk.Label] = None
        self._help_btn: Optional[tk.Label] = None
        self._settings_btn: Optional[tk.Label] = None
        self._run_btn: Optional[tk.Label] = None
        self._build_btn: Optional[tk.Label] = None
        self._clean_btn: Optional[tk.Label] = None
        self._package_btn: Optional[tk.Label] = None
        self._history_btn: Optional[tk.Label] = None
        self._versions_btn: Optional[tk.Label] = None

        # Sidebar Panels
        self._run_chevron: Optional[tk.Label] = None
        self._run_body: Optional[tk.Frame] = None
        self._run_scripts_frame: Optional[tk.Frame] = None
        self._run_open: bool = False
        self._ver_chevron: Optional[tk.Label] = None
        self._ver_body: Optional[tk.Frame] = None
        self._ver_list_frame: Optional[tk.Frame] = None
        self._ver_open: bool = False
        self._bump_var = tk.StringVar(value="patch")

        # Topbar / Statusbar widgets
        self._conn_dot: Optional[tk.Frame] = None
        self._sb_langs_frame: Optional[tk.Frame] = None
        self._sb_parsed_var = tk.StringVar(value="")
        self._metrics_badge: Optional[tk.Label] = None
        self._metrics_badge_var = tk.StringVar(value="")
        self._self_badge: Optional[tk.Label] = None
        self._self_badge_var = tk.StringVar(value="")
        self._proc_badge: Optional[tk.Label] = None
        self._proc_badge_var = tk.StringVar(value="")
        self._proc_count_var = tk.StringVar(value="0")

        # Watchers & Systems
        self._metrics_watcher: Optional[MetricsWatcher] = None
        self._proc_mgr: Optional[ProcessManager] = None
        self._proc_monitor: Optional[ProcessMonitor] = None
        self._sandboxes: dict[str, SandboxRun] = {}
        self._proc_win: Optional[tk.Toplevel] = None
        self._proc_log_widgets: dict[str, tk.Text] = {}
        self._proc_cmd_var = tk.StringVar()
        self._proc_cwd_var = tk.StringVar()
        self.project_root: str = ""
        self._proc_list_canvas: Optional[tk.Canvas] = None
        self._proc_list_inner: Optional[tk.Frame] = None
        self._warnings_index: Dict[str, List[Any]] = {}
        self._log_win: Optional[tk.Toplevel] = None
        self._log_text: Optional[tk.Text] = None
        self._editors: dict[str, EditorWindow] = {}
        self._file_metrics: dict = {}

        # Build Panel
        self._build_win: Optional[tk.Toplevel] = None
        self._build_log_text: Optional[tk.Text] = None
        self._build_kind_var = tk.StringVar(value="tarball")
        self._build_plat_var = tk.StringVar(value="auto")
        self._build_bump_var = tk.StringVar(value="none")
        self._build_minify_var = tk.BooleanVar(value=True)
        self._build_clean_var = tk.BooleanVar(value=True)
        self._build_tests_var = tk.BooleanVar(value=False)
        self._perf_frame: Optional[tk.Frame] = None
        self._render_timing_active: bool = False
        self._render_timing_frame: Optional[tk.Frame] = None
        self._render_timing_after_id: Optional[str] = None
        
        # Loading Overlay
        self._loading_win: Optional[tk.Toplevel] = None
        self._loading_progress: int = 0
        self._loading_msg: Optional[tk.Label] = None
        self._loading_fill: Optional[tk.Frame] = None
        self._loading_after_id: Optional[str] = None
        
        # IDs for after()
        self._poll_watcher_id: Optional[str] = None
        self._side_metrics_after_id: Optional[str] = None
        self._redraw_after_id: Optional[str] = None
        self._resize_after_id: Optional[str] = None

        # Render cache
        self._cache_nodes: list | None = None
        self._cache_edges: list | None = None
        self._cache_node_map: dict | None = None
        self._hit_boxes: dict = {}
        self._redraw_pending: bool = False
        self._render_times: list = []

        self._is_self: bool = False
        self._sidebar_scroll: Optional[tk.Scrollbar] = None
        self._sidebar_canvas: Optional[tk.Canvas] = None
        self._sidebar_inner:  Optional[tk.Frame] = None
        
        # Additional state
        self._log_path: str = ""
        self._log_file: str = ""

        self._load_terminal_history()

        # ── Build UI ──────────────────────────────────────────────────────────
        self._teams_log: Optional[tk.Text] = None
        self._sess_list: Optional[tk.Listbox] = None
        self._sess_data: list = []
        self._teams_init()
        self._build_ui()
        self._bind_keys()
        self._load_saved_projects()

        # Clean shutdown — stop all processes and monitor on window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)


    def _start_metrics_watcher(self, project_root: str) -> None:
        """Start (or restart) MetricsWatcher for the loaded project."""
        # Stop existing watcher
        mw = self._metrics_watcher
        if mw is not None:
            try:
                mw.stop()
            except Exception:
                pass
        mw = MetricsWatcher(project_root)
        self._metrics_watcher = mw
        if mw:
            mw.start()
        if self._log:
            self._log.debug("MetricsWatcher started for %s", project_root)
        # Schedule periodic refresh of the metrics cache
        self._schedule_metrics_refresh()

    def _schedule_metrics_refresh(self) -> None:
        """Refresh file metrics cache every 1.5s while a project is loaded."""
        if not self.graph:
            return
        self._refresh_file_metrics()
        self.after(1500, self._schedule_metrics_refresh)

    def _refresh_file_metrics(self) -> None:
        """Pull latest file metrics from the watcher into _file_metrics."""
        mw = self._metrics_watcher
        if not mw:
            return
        new_metrics = mw.get_file_metrics()
        changed = (new_metrics != self._file_metrics)
        self._file_metrics = new_metrics
        # Only trigger a redraw if metrics actually changed
        if changed and self.graph:
            self._schedule_redraw()

    def _node_metrics(self, node_path: str) -> Dict[str, Any]:
        """Return timing metrics for a node path, or empty dict if unavailable."""
        if not self._file_metrics:
            return {}
        # Try exact match first, then basename match
        m = self._file_metrics.get(node_path)
        if m and isinstance(m, dict):
            return m
        # Normalise separators for cross-platform
        norm = node_path.replace("\\", "/")
        for key, val in self._file_metrics.items():
            if not isinstance(key, str): continue
            if key.replace("\\", "/").endswith(norm) or norm.endswith(key.replace("\\", "/")):
                return val if isinstance(val, dict) else {}
        return {}

    # ── Editor ───────────────────────────────────────────────────────────────

    def _open_editor(self, node=None, filepath='', line=0):
        '''Open or focus the syntax-highlighted editor for a source file.'''
        if node:
            root = self.graph['meta']['root'] if self.graph else ''
            filepath = node.get('fullPath') or os.path.join(root, node['path'])
        if not filepath or not os.path.isfile(filepath):
            return
        existing = self._editors.get(filepath)
        if existing:
            try:
                existing.focus()
                if line:
                    existing.scroll_to_line(line)
                return
            except Exception:
                self._editors.pop(filepath, None)
        ed = EditorWindow(
            master=self, filepath=filepath,
            project_root=self.graph['meta']['root'] if self.graph else '',
            read_only=True,
            on_save=self._on_editor_save,
            on_ask_ai=self._on_editor_ask_ai if self._ai_available else None,
            theme=dict(P),
        )
        self._editors[filepath] = ed
        if line:
            self.after(100, lambda: ed.scroll_to_line(line))
        def _cleanup(fp=filepath):
            self._editors.pop(fp, None)
        ed.win.protocol('WM_DELETE_WINDOW', lambda: (_cleanup(), ed.win.destroy()))

    def _on_editor_save(self, filepath, content):
        self._log.info('Editor saved %s — re-parsing', filepath)
        if self.graph:
            self._load_project(self.graph['meta']['root'])

    def _on_editor_ask_ai(self, filepath, content):
        if self.graph:
            rel = os.path.relpath(filepath, self.graph['meta']['root'])
        else:
            rel = os.path.basename(filepath)
        self._toggle_ai_panel()
        if hasattr(self, '_ai_input_var'):
            self._ai_input_var.set(f'Review {rel} and identify improvements.')

    # ── Canvas double-click / right-click ─────────────────────────────────────

    def _canvas_double_click(self, event):
        if getattr(self, 'canvas_mode', 'graph') == 'teams':
            self._tw_canvas_double_click(event); return
        nid = self._hit_test_node(event.x, event.y)
        if not nid:
            return
        node = self._node_map().get(nid)
        if node and not node.get('isExternal'):
            self._open_editor(node=node)

    def _canvas_right_click(self, event):
        if getattr(self, 'canvas_mode', 'graph') == 'teams':
            self._tw_canvas_right_click(event); return
        nid = self._hit_test_node(event.x, event.y)
        if not nid:
            return
        node = self._node_map().get(nid)
        if not node:
            return
        menu = tk.Menu(self, tearoff=0, bg=P['bg2'], fg=P['t1'],
                       activebackground=P['bg3'], activeforeground=P['t0'],
                       bd=0, relief='flat', font=self._mono_xs)
        menu.add_command(label=f"  {node.get('label', nid)}",
                         state='disabled', font=self._mono_s)
        menu.add_separator()
        if not node.get('isExternal'):
            menu.add_command(label='  Open in Editor',
                             command=lambda: self._open_editor(node=node))
        menu.add_command(label='  Inspect',
                         command=lambda: self._inspect_node(node))
        if self._ai_available and not node.get('isExternal'):
            menu.add_separator()
            root = self.graph['meta']['root'] if self.graph else ''
            fp   = os.path.join(root, node.get('path', ''))
            menu.add_command(label='  Ask AI about this file',
                             command=lambda: self._on_editor_ask_ai(fp, ''))
        menu.tk_popup(event.x_root, event.y_root)


    def _ai_clear(self):
        self._ai_messages = []
        w = getattr(self, '_ai_conv', None)
        if w:
            try:
                w.config(state='normal')
                w.delete('1.0', 'end')
                self.after(0, lambda: self._ai_append('Conversation cleared.\n\n', 'dim'))
                w.config(state='disabled')
            except Exception:
                pass

    def _ai_send(self):
        if not hasattr(self, '_ai_input_var'):
            return
        prompt = self._ai_input_var.get().strip()
        if not prompt:
            return
        if not self._ai_available:
            self._ai_append('Ollama not running. Start with: ollama serve\n', 'error')
            return
        self._ai_input_var.set('')
        self._ai_append(f'\nYou: {prompt}\n', 'user')
        self._ensure_manager()
        mgr = self._manager
        if mgr is not None:
            # Detect inline bake request ("bake for 10 minutes on ...")
            import re as _re
            bm = _re.search(r'\bbake\b.*?(\d+)\s*min', prompt, _re.I)
            if bm:
                mgr.bake(task=prompt, minutes=int(bm.group(1)))
                self._bake_start(int(bm.group(1)))
            else:
                mgr.send(prompt)

    def _ai_append_content(self, text):
        """Robust stateful streaming append with debug logging and regex."""
        # Debug log raw chunk
        try:
            with open('/tmp/side_ai_raw.log', 'a') as f:
                f.write(f"--- CHUNK ({len(text)}) ---\n{text}\n")
        except: pass

        self._ai_state['buffer'] += text
        
        while True:
            buf = str(self._ai_state['buffer'])
            if not buf: break
            
            if not self._ai_state['in_thought'] and not self._ai_state['in_code']:
                # Looking for start of thought or code
                match_t = re.search(r'<thought>', buf, re.I)
                match_c = re.search(r'```', buf)
                
                if match_t and (not match_c or match_t.start() < match_c.start()):
                    pre = buf[0:match_t.start()]
                    if pre: self._ai_append_md_flat(str(pre))
                    self._ai_append("\n[THOUGHT: ", 'thought')
                    self._ai_state['in_thought'] = True
                    self._ai_state['buffer'] = buf[match_t.end():]
                    continue
                elif match_c:
                    pre = buf[0:match_c.start()]
                    if pre: self._ai_append_md_flat(pre)
                    self._ai_state['in_code'] = True
                    self._ai_append("\n", 'ai')
                    self._ai_state['buffer'] = buf[match_c.end():]
                    continue
                
                # NO MATCH FOUND in current buffer.
                # BUT: if it ends with '<' or '`', it might be a partial tag.
                if any(buf.lower().endswith(t) for t in ['<','<t','<th','<tho','<thou','<thoug','<thought', '`', '``']):
                    # Keep the partial tag in buffer, print everything else
                    idx = buf.rfind('<') if '<' in buf else buf.rfind('`')
                    pre = buf[:idx]
                    if pre: self._ai_append_md_flat(pre)
                    self._ai_state['buffer'] = buf[idx:]
                    break
                else:
                    self._ai_append_md_flat(buf)
                    self._ai_state['buffer'] = ""
                    break
            
            elif self._ai_state['in_thought']:
                match_te = re.search(r'</thought>', buf, re.I)
                if match_te:
                    content = buf[:match_te.start()]
                    if content: self._ai_append(content.strip(), 'thought')
                    self._ai_append("]\n", 'thought')
                    self._ai_state['in_thought'] = False
                    self._ai_state['buffer'] = buf[match_te.end():]
                    continue
                else:
                    # Partial tag check for </thought>
                    if any(buf.lower().endswith(t) for t in ['<','</','</t','</th','</tho','</thou','</thoug','</thought']):
                        # Print everything up to the potential start of the closing tag
                        idx = buf.lower().rfind('<')
                        pre = buf[:idx]
                        if pre: self._ai_append(pre, 'thought')
                        self._ai_state['buffer'] = buf[idx:]
                        break
                    self._ai_append(buf, 'thought')
                    self._ai_state['buffer'] = ""
                    break
            
            elif self._ai_state['in_code']:
                match_ce = re.search(r'```', buf)
                if match_ce:
                    content = buf[:match_ce.start()]
                    if content: self._ai_append(content, 'block')
                    self._ai_state['in_code'] = False
                    self._ai_state['buffer'] = buf[match_ce.end():]
                    continue
                else:
                    if any(buf.endswith(t) for t in ['`', '``']):
                        idx = buf.rfind('`')
                        pre = buf[:idx]
                        if pre: self._ai_append(pre, 'block')
                        self._ai_state['buffer'] = buf[idx:]
                        break
                    self._ai_append(buf, 'block')
                    self._ai_state['buffer'] = ""
                    break

    def _ai_append_md_flat(self, text):
        """Append text while handling bold and inline code styling."""
        # This is a basic regex-free parser for streaming-friendly growth
        parts = re.split(r'(\*\*|`)', text)
        current_tag = 'ai'
        
        for part in parts:
            if part == '**':
                self._ai_state['in_bold'] = not self._ai_state['in_bold']
            elif part == '`':
                # Toggle code tag (nested is ignored for simplicity)
                pass 
            else:
                tag = 'ai'
                if self._ai_state.get('in_bold'): tag = 'bold'
                # Note: inline code not fully stateful yet, just bold for now
                self._ai_append(part, tag)

    def _ai_reset_stream_state(self):
        self._ai_state = {
            'in_thought': False,
            'in_code': False,
            'in_bold': False,
            'buffer': ""
        }


    def _on_close(self) -> None:
        """Graceful shutdown — stop processes and monitor before exit."""
        self._log.info("S-IDE shutting down")
        mon = getattr(self, "_proc_monitor", None)
        if mon:
            try: mon.stop()
            except Exception: pass
        mw = getattr(self, "_metrics_watcher", None)
        if mw:
            try: mw.stop()
            except Exception: pass
        if self._proc_mgr:
            try: self._proc_mgr.stop_all()
            except Exception: pass
        self._save_terminal_history()
        self.destroy()

    def _best_font(self, families, size, bold=False):
        available = set(tkfont.families())
        for fam in families:
            if fam in available:
                return tkfont.Font(family=fam, size=size,
                                   weight="bold" if bold else "normal")
        return tkfont.Font(family="Courier", size=size,
                           weight="bold" if bold else "normal")

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.rowconfigure(0, weight=0)  # topbar
        self.rowconfigure(1, weight=1)  # main
        self.rowconfigure(2, weight=0)  # statusbar
        self.columnconfigure(0, weight=1)

        self._build_topbar()
        self._build_main()
        self._build_statusbar()

    def _build_topbar(self):
        tb = tk.Frame(self, bg=P["bg1"], height=44)
        tb.grid(row=0, column=0, sticky="ew")
        tb.pack_propagate(False)
        tb.columnconfigure(99, weight=1)  # spacer

        col = [0]
        def add(widget, padx=(2, 2)):
            widget.grid(row=0, column=col[0], padx=padx, pady=6, in_=tb, sticky="ns")
            col[0] += 1

        # Logo mark — 4-colour grid
        logo_frame = tk.Frame(tb, bg=P["bg1"])
        logo_frame.grid(row=0, column=col[0], padx=(12, 6), pady=10)
        col[0] += 1
        lcolours = [P["green"], P["blue"], P["amber"], P["purple"]]
        for i, c in enumerate(lcolours):
            dot = tk.Frame(logo_frame, bg=c, width=6, height=6)
            dot.grid(row=i // 2, column=i % 2, padx=1, pady=1)

        # Brand name
        tk.Label(tb, text="S-IDE", bg=P["bg1"], fg=P["t0"],
                 font=self._mono_l).grid(row=0, column=col[0], padx=(0, 2))
        col[0] += 1
        tk.Label(tb, text="v0.1", bg=P["bg1"], fg=P["t3"],
                 font=self._mono_xs).grid(row=0, column=col[0], padx=(0, 10))
        col[0] += 1

        # Separator
        tk.Frame(tb, bg=P["line2"], width=1).grid(row=0, column=col[0],
                 sticky="ns", padx=4, pady=8)
        col[0] += 1

        # Project name (clickable)
        self._lbl_project = tk.Label(tb, text="—", bg=P["bg1"], fg=P["t2"],
                                      font=self._mono_s, cursor="hand2")
        self._lbl_project.grid(row=0, column=col[0], padx=(4, 10))
        self._lbl_project.bind("<Button-1>", lambda _: self._open_project_dialog())
        col[0] += 1

        # Doc health badge
        self._doc_badge_var = tk.StringVar(value="")
        self._doc_badge = tk.Label(tb, textvariable=self._doc_badge_var,
                                    bg=P["bg3"], fg=P["amber"],
                                    font=self._mono_xs, cursor="hand2",
                                    padx=6, pady=2)
        self._doc_badge.grid(row=0, column=col[0], padx=4)
        self._doc_badge.bind("<Button-1>", lambda _: self._inspect_doc_health())
        self._doc_badge.grid_remove()
        col[0] += 1

        # Spacer
        spacer = tk.Frame(tb, bg=P["bg1"])
        spacer.grid(row=0, column=col[0], sticky="ew")
        tb.columnconfigure(col[0], weight=1)
        col[0] += 1

        # Search
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search)
        search_frame = tk.Frame(tb, bg=P["bg3"],
                                bd=1, relief="flat",
                                highlightbackground=P["line2"],
                                highlightthickness=1)
        search_frame.grid(row=0, column=col[0], padx=6, pady=8)
        col[0] += 1
        tk.Label(search_frame, text="⌕", bg=P["bg3"], fg=P["t2"],
                 font=self._mono_s).pack(side="left", padx=(4, 2))
        search_entry = tk.Entry(search_frame, textvariable=self._search_var,
                                 bg=P["bg3"], fg=P["t1"], insertbackground=P["green"],
                                 bd=0, font=self._mono_s, width=16)
        search_entry.pack(side="left", padx=(0, 6))

        # Category filter chips
        self._cat_btns = {}
        cats = [("ALL", ""), ("PY", "python"), ("JS", "javascript"),
                ("TS", "typescript"), ("CFG", "config"), ("DOCS", "docs")]
        for label, cat_key in cats:
            btn = tk.Label(tb, text=label, bg=P["bg3"], fg=P["t2"],
                           font=self._mono_xs, padx=7, pady=3,
                           cursor="hand2",
                           highlightbackground=P["line2"], highlightthickness=1)
            btn.grid(row=0, column=col[0], padx=2, pady=8)
            col[0] += 1
            btn.bind("<Button-1>", lambda _, k=cat_key: self._toggle_filter_cat(k))
            self._cat_btns[cat_key] = btn
        self._update_cat_chips()

        # EXT toggle
        self._ext_btn = tk.Label(tb, text="EXT", bg=P["bg3"], fg=P["green"],
                                  font=self._mono_xs, padx=7, pady=3,
                                  cursor="hand2",
                                  highlightbackground=P["green2"],
                                  highlightthickness=1)
        self._ext_btn.grid(row=0, column=col[0], padx=2, pady=8)
        if self._ext_btn:
            self._ext_btn.bind("<Button-1>", self._toggle_ext)
        col[0] += 1

        # FIT
        fit_btn = tk.Label(tb, text="FIT", bg=P["bg3"], fg=P["t2"],
                           font=self._mono_xs, padx=7, pady=3,
                           cursor="hand2",
                           highlightbackground=P["line2"], highlightthickness=1)
        fit_btn.grid(row=0, column=col[0], padx=2, pady=8)
        fit_btn.bind("<Button-1>", lambda _: self._fit_view())
        col[0] += 1

        # Teams designer toggle
        self._teams_btn = tk.Label(tb, text="⚡ TEAMS", bg=P["bg3"], fg=P["t2"],
                                    font=self._mono_xs, padx=7, pady=3,
                                    cursor="hand2",
                                    highlightbackground=P["line2"], highlightthickness=1)
        self._teams_btn.grid(row=0, column=col[0], padx=(2, 6), pady=8)
        self._teams_btn.bind("<Button-1>", lambda _: self._toggle_teams_mode())
        col[0] += 1


        log_btn = tk.Label(tb, text="LOG", bg=P["bg3"], fg=P["t2"],
                           font=self._mono_xs, padx=7, pady=3,
                           cursor="hand2",
                           highlightbackground=P["line2"], highlightthickness=1)
        log_btn.grid(row=0, column=col[0], padx=(2, 6), pady=8)
        log_btn.bind("<Button-1>", lambda _: self._toggle_log_panel())
        col[0] += 1

        # AI
        self._ai_btn = tk.Label(tb, text="✦ AI", bg=P["bg3"], fg=P["t2"],
                                 font=self._mono_xs, padx=7, pady=3,
                                 cursor="hand2",
                                 highlightbackground=P["line2"], highlightthickness=1)
        self._ai_btn.grid(row=0, column=col[0], padx=(2, 2), pady=8)
        self._ai_btn.bind("<Button-1>", lambda _: self._toggle_ai_panel())
        col[0] += 1

        # BUILD
        build_btn = tk.Label(tb, text="🔨 BUILD", bg=P["bg3"], fg=P["t2"],
                              font=self._mono_xs, padx=7, pady=3,
                              cursor="hand2",
                              highlightbackground=P["line2"], highlightthickness=1)
        build_btn.grid(row=0, column=col[0], padx=(2, 6), pady=8)
        build_btn.bind("<Button-1>", lambda _: self._toggle_build_panel())
        col[0] += 1

        # Zoom label
        self._zoom_var = tk.StringVar(value="100%")
        tk.Label(tb, textvariable=self._zoom_var, bg=P["bg1"], fg=P["t3"],
                 font=self._mono_xs, width=5, anchor="e").grid(row=0, column=col[0],
                 padx=(0, 12))

    def _build_main(self):
        main = tk.Frame(self, bg=P["bg0"])
        main.grid(row=1, column=0, sticky="nsew")
        main.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)

        self._main_pw = tk.PanedWindow(main, orient="vertical", bg=P["line2"], bd=0, sashwidth=4, sashpad=0)
        self._main_pw.grid(row=0, column=0, sticky="nsew")

        # Top half (canvas and inspector)
        top_frame = tk.Frame(self._main_pw, bg=P["bg0"])
        top_frame.rowconfigure(0, weight=1)
        top_frame.columnconfigure(0, weight=1)  # Canvas column
        self._main_pw.add(top_frame, stretch="always")

        self._build_canvas(top_frame)
        self._build_inspector(top_frame)

        # Bottom half (tabbed panel)
        self._build_bottom_panel(self._main_pw)

    def _build_bottom_panel(self, parent_pw):
        self._bottom_wrapper = tk.Frame(parent_pw, bg=P["bg1"])
        self._bottom_wrapper.rowconfigure(1, weight=1)
        self._bottom_wrapper.columnconfigure(0, weight=1)
        
        # Add to panedwindow
        parent_pw.add(self._bottom_wrapper, height=250, stretch="never")

        # Tab bar header + collapse button
        tb = tk.Frame(self._bottom_wrapper, bg=P["bg2"])
        tb.grid(row=0, column=0, sticky="ew")

        self._bottom_expanded = True
        self._saved_bottom_height = 250
        
        def _toggle_bottom():
            self._bottom_expanded = not self._bottom_expanded
            if self._bottom_expanded:
                self._bottom_body.grid()
                parent_pw.paneconfigure(self._bottom_wrapper, height=self._saved_bottom_height)
                col_btn.config(text="▼")
            else:
                self._saved_bottom_height = self._bottom_wrapper.winfo_height()
                self._bottom_body.grid_remove()
                parent_pw.paneconfigure(self._bottom_wrapper, height=28)
                col_btn.config(text="▲")
        
        col_btn = tk.Label(tb, text="▼", bg=P["bg2"], fg=P["t2"], font=self._mono_s, padx=12, pady=4, cursor="hand2")
        col_btn.pack(side="right")
        col_btn.bind("<Button-1>", lambda _: _toggle_bottom())

        self._bottom_body = tk.Frame(self._bottom_wrapper, bg=P["bg1"])
        self._bottom_body.grid(row=1, column=0, sticky="nsew")

        # Tabs dict
        self._tabs = {}
        self._current_tab = None

        def add_tab(name, title):
            btn = tk.Label(tb, text=title, bg=P["bg2"], fg=P["t2"], font=self._mono_s, padx=16, pady=4, cursor="hand2")
            btn.pack(side="left")
            btn.bind("<Button-1>", lambda _: self._select_bottom_tab(name))
            frm = tk.Frame(self._bottom_body, bg=P["bg1"])
            self._tabs[name] = {"btn": btn, "frame": frm}
            return frm

        # Create panels
        self._proj_tab   = add_tab("projects", "Projects")
        self._ai_tab     = add_tab("ai", "AI Chat")
        self._plan_tab   = add_tab("plan", "Plan")
        self._play_tab   = add_tab("playground", "Playground")
        self._editor_tab = add_tab("editor", "Editor")
        self._term_tab   = add_tab("terminal",  "Terminal")
        self._teams_tab  = add_tab("teams",     "Teams Log")

        # Build inside the panels
        self._build_sidebar(self._proj_tab)
        self._build_ai_panel(self._ai_tab)
        self._build_plan_panel(self._plan_tab)
        self._build_playground_panel(self._play_tab)
        self._build_terminal_panel(self._term_tab)
        self._build_teams_log_panel(self._teams_tab)

        # Select first tab
        self._select_bottom_tab("projects")

    def _select_bottom_tab(self, name):
        if self._current_tab is not None and self._current_tab in self._tabs:
            prev = self._tabs[self._current_tab]
            prev["btn"].config(bg=P["bg2"], fg=P["t2"])
            prev["frame"].pack_forget()
        
        self._current_tab = name
        cur = self._tabs[name]
        cur["btn"].config(bg=P["bg3"], fg=P["t0"])
        cur["frame"].pack(fill="both", expand=True)

        if not self._bottom_expanded:
            # Force expand if user clicks a tab while collapsed
            self._bottom_expanded = True
            self._bottom_body.grid()
            self._main_pw.paneconfigure(self._bottom_wrapper, height=self._saved_bottom_height)
            # Find the toggle button and update its text (it is the last child in tb frame)
            # Since we just need the visual update, it's ok. Using a global ref in method is cleaner.
            for widget in self._bottom_wrapper.winfo_children()[0].winfo_children():
                if widget.cget("text") == "▲":
                    widget.config(text="▼")
        
        # Ensure focus after visibility change
        self.update_idletasks()
        if name == "ai" and hasattr(self, "_ai_input"):
            self.after(50, self._ai_input.focus_force)
        elif name == "terminal" and hasattr(self, "_term_input"):
            self.after(50, self._term_input.focus_force)
        elif name == "playground" and hasattr(self, "_play_text"):
            self.after(50, self._play_text.focus_force)

    # ── Terminal panel ────────────────────────────────────────────────────────

    def _build_terminal_panel(self, parent):
        # Input (pack bottom first)
        tk.Frame(parent, bg=P['line'], height=1).pack(fill='x', side='bottom')
        inp_f = tk.Frame(parent, bg=P['bg2'])
        inp_f.pack(fill='x', side='bottom', padx=10, pady=8)

        # Prompt label
        tk.Label(inp_f, text="$", bg=P['bg2'], fg=P['t2'], font=self._mono_xs).pack(side='left', padx=(0, 5))

        self._term_input_var = tk.StringVar()
        self._term_input = tk.Entry(inp_f, textvariable=self._term_input_var,
                       bg=P['bg3'], fg=P['t0'], insertbackground=P['green'],
                       bd=0, font=(self._mono_xs.actual()['family'], 11), width=50)
        self._term_input.pack(side='left', fill='x', expand=True, ipady=5, padx=(0, 8))

        # Output area (takes middle space)
        co = tk.Frame(parent, bg=P['bg0'])
        co.pack(fill='both', expand=True, side='top')

        def _show_term_res(out, err):
            if not self._term_out or not self._term_out.winfo_exists(): return
            self._term_out.config(state='normal')
            if out:
                self._term_out.insert('end', out + ('\n' if not out.endswith('\n') else ''))
            if err:
                self._term_out.insert('end', err + ('\n' if not err.endswith('\n') else ''), 'err')
            self._term_out.config(state='disabled')
            self._term_out.see('end')

        def _run_term(event=None):
            cmd = self._term_input_var.get().strip()
            if not cmd: return
            
            # History
            if not self._term_history or self._term_history[-1] != cmd:
                self._term_history.append(cmd)
                self._save_terminal_history()
            self._term_history_idx = -1

            self._term_input_var.set('')
            self._term_out.config(state='normal')
            self._term_out.insert('end', f'$ {cmd}\n', 'cmd')
            self._term_out.config(state='disabled')
            self._term_out.see('end')

            def run_proc():
                root = self.graph['meta']['root'] if self.graph else os.getcwd()
                try:
                    proc = subprocess.Popen(cmd, shell=True, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    out, err = proc.communicate()
                    self.after(0, _show_term_res, out, err)
                except Exception as e:
                    self.after(0, _show_term_res, '', str(e))

            threading.Thread(target=run_proc, daemon=True).start()

        self._term_input.bind("<Return>", _run_term)
        self._term_input.bind("<Up>",       lambda _: self._term_cycle_history(-1))
        self._term_input.bind("<Down>",     lambda _: self._term_cycle_history(1))
        self._term_input.bind("<Button-1>", lambda _: self._term_input.focus_force())
        
        btn = tk.Label(inp_f, text='Run', bg=P['green2'], fg=P['green'],
                       font=self._mono_s, padx=10, pady=5, cursor='hand2',
                       highlightbackground=P['green'], highlightthickness=1)
        btn.pack(side='left')
        btn.bind('<Button-1>', _run_term)

    def _term_cycle_history(self, delta):
        if not self._term_history: return
        if self._term_history_idx == -1:
             self._term_current_draft = self._term_input_var.get()
        
        self._term_history_idx -= delta
        if self._term_history_idx < 0:
            self._term_history_idx = -1
            self._term_input_var.set(getattr(self, "_term_current_draft", ""))
            return
        
        if self._term_history_idx >= len(self._term_history):
            self._term_history_idx = len(self._term_history) - 1
            
        cmd = self._term_history[len(self._term_history) - 1 - self._term_history_idx]
        self._term_input_var.set(cmd)

    def _save_terminal_history(self):
        try:
            path = os.path.expanduser("~/.s_ide_terminal_history.json")
            with open(path, "w") as f:
                json.dump(self._term_history[-100:], f)
        except Exception: pass

    def _load_terminal_history(self):
        try:
            path = os.path.expanduser("~/.s_ide_terminal_history.json")
            if os.path.exists(path):
                with open(path, "r") as f:
                    self._term_history = json.load(f)
            else:
                self._term_history = []
        except Exception:
            self._term_history = []


    def _build_sidebar(self, parent):
        self._sidebar = tk.Frame(parent, bg=P["bg1"])
        self._sidebar.pack(fill="both", expand=True)

        # Header
        hdr = tk.Frame(self._sidebar, bg=P["bg1"])
        hdr.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(hdr, text="PROJECTS", bg=P["bg1"], fg=P["t3"],
                 font=self._mono_xs).pack(side="left")
        add_btn = tk.Label(hdr, text="+ Open", bg=P["bg3"], fg=P["t2"],
                           font=self._mono_xs, padx=5, pady=2, cursor="hand2",
                           highlightbackground=P["line2"], highlightthickness=1)
        add_btn.pack(side="right")
        add_btn.bind("<Button-1>", lambda _: self._open_project_dialog())

        new_btn = tk.Label(hdr, text="+ New", bg=P["bg3"], fg=P["t2"],
                           font=self._mono_xs, padx=5, pady=2, cursor="hand2",
                           highlightbackground=P["line2"], highlightthickness=1)
        new_btn.pack(side="right", padx=(0, 3))
        new_btn.bind("<Button-1>", lambda _: self._new_project_dialog())

        # Separator
        tk.Frame(self._sidebar, bg=P["line"], height=1).pack(fill="x")

        # Project list (scrollable)
        list_frame = tk.Frame(self._sidebar, bg=P["bg1"])
        list_frame.pack(fill="both", expand=True)
        self._proj_list_frame = list_frame

        # Separator
        tk.Frame(self._sidebar, bg=P["line"], height=1).pack(fill="x")

        # Open folder button
        open_btn = tk.Frame(self._sidebar, bg=P["bg1"], cursor="hand2")
        open_btn.pack(fill="x", padx=8, pady=6)
        open_inner = tk.Frame(open_btn, bg=P["bg3"],
                               highlightbackground=P["line2"], highlightthickness=1)
        open_inner.pack(fill="x", padx=0)
        tk.Label(open_inner, text="📁  open project folder",
                 bg=P["bg3"], fg=P["t2"], font=self._mono_xs,
                 pady=8).pack()
        open_inner.bind("<Button-1>", lambda _: self._open_project_dialog())
        for child in open_inner.winfo_children():
            child.bind("<Button-1>", lambda _: self._open_project_dialog())

        # Separator
        tk.Frame(self._sidebar, bg=P["line"], height=1).pack(fill="x")

        # Run scripts panel
        self._build_run_panel()

        # Separator
        tk.Frame(self._sidebar, bg=P["line"], height=1).pack(fill="x")

        # Version panel
        self._build_version_panel()

    def _build_run_panel(self):
        """Sidebar panel: run/stop scripts defined in side.project.json."""
        run_frame = tk.Frame(self._sidebar, bg=P["bg1"])
        run_frame.pack(fill="x")

        # Header
        hdr = tk.Frame(run_frame, bg=P["bg1"], cursor="hand2")
        hdr.pack(fill="x")
        tk.Label(hdr, text="RUN", bg=P["bg1"], fg=P["t3"],
                 font=self._mono_xs, padx=10, pady=6).pack(side="left")
        # Always create chevron so bindings are safe.
        self._run_chevron = tk.Label(
            hdr, text="▸", bg=P["bg1"], fg=P["t3"], font=self._mono_xs, padx=8
        )
        self._run_chevron.pack(side="right")

        self._run_body = tk.Frame(run_frame, bg=P["bg1"])
        self._run_scripts_frame = tk.Frame(self._run_body, bg=P["bg1"])
        self._run_scripts_frame.pack(fill="x", padx=8, pady=(4, 6))
        self._run_open = False

        def _toggle(_event=None):
            self._run_open = not self._run_open
            if self._run_open:
                self._run_body.pack(fill="x")
                self._run_chevron.config(text="▾")
                self._refresh_run_scripts()
            else:
                self._run_body.pack_forget()
                self._run_chevron.config(text="▸")

        hdr.bind("<Button-1>", _toggle)
        self._run_chevron.bind("<Button-1>", _toggle)

    def _refresh_run_scripts(self):
        """Rebuild the run script buttons from the current project config."""
        for w in self._run_scripts_frame.winfo_children():
            w.destroy()
        if not self.graph:
            tk.Label(self._run_scripts_frame, text="no project loaded",
                     bg=P["bg1"], fg=P["t3"], font=self._mono_xs).pack(anchor="w")
            return

        scripts = self.graph.get("meta", {}).get("project", {}).get("run", {})
        if not scripts:
            tk.Label(self._run_scripts_frame, text="no run scripts defined",
                     bg=P["bg1"], fg=P["t3"], font=self._mono_xs).pack(anchor="w")
            return

        running_cmds  = {p.command for p in self.processes.values()
                         if p.info()["status"] == "running"}
        sandbox_cmds  = {sb.proc.command for sb in
                          getattr(self, "_sandboxes", {}).values()
                          if sb.is_running}

        for script_name, command in scripts.items():
            row = tk.Frame(self._run_scripts_frame, bg=P["bg1"])
            row.pack(fill="x", pady=2)

            is_running     = command in running_cmds
            is_sandboxed   = command in sandbox_cmds
            status_col     = P["cyan"] if is_sandboxed else (P["green"] if is_running else P["t3"])
            tk.Frame(row, bg=status_col, width=6, height=6).pack(side="left", padx=(0, 6))

            tk.Label(row, text=script_name, bg=P["bg1"], fg=P["t1"],
                     font=self._mono_s).pack(side="left", fill="x", expand=True)

            if is_running or is_sandboxed:
                label = "■ stop"
                proc = next((p for p in self.processes.values()
                             if p.command == command
                             and p.info()["status"] == "running"), None)
                sb_key = next((k for k, sb in
                               getattr(self, "_sandboxes", {}).items()
                               if sb.is_running and sb._proc and sb._proc.command == command),
                              None)
                stop = tk.Label(row, text=label, bg=P["bg3"], fg=P["red"],
                                font=self._mono_xs, padx=5, pady=2, cursor="hand2",
                                highlightbackground=P["red"], highlightthickness=1)
                stop.pack(side="right", padx=2)
                def _stop_action(p=proc, sk=sb_key):
                    if p: p.stop()
                    if sk:
                        sb = self._sandboxes.get(sk)
                        if sb:
                            log_dir = sb.cleanup()
                            if log_dir:
                                self._log.info("Sandbox logs saved: %s", log_dir)
                            del self._sandboxes[sk]
                    self.after(300, self._refresh_run_scripts)
                stop.bind("<Button-1>", lambda _, fn=_stop_action: fn())
            else:
                # Run buttons: normal / clean / minified
                def _btn(text, col, bdr, fn):
                    b = tk.Label(row, text=text, bg=P["bg3"], fg=col,
                                 font=self._mono_xs, padx=5, pady=2, cursor="hand2",
                                 highlightbackground=bdr, highlightthickness=1)
                    b.pack(side="right", padx=1)
                    b.bind("<Button-1>", lambda _, f=fn: f())
                    return b

                _btn("▶", P["green"], P["green2"],
                     lambda sn=script_name, cmd=command: self._run_script(sn, cmd))
                _btn("🧹", P["cyan"], P["line2"],
                     lambda sn=script_name, cmd=command: self._run_sandbox(sn, cmd, "clean"))
                _btn("⚡", P["purple"], P["line2"],
                     lambda sn=script_name, cmd=command: self._run_sandbox(sn, cmd, "minified"))

        # ── Tool row: Instrument + rollback ────────────────────────────────
        tk.Frame(self._run_scripts_frame, bg=P["line"], height=1).pack(
            fill="x", pady=(6, 4))
        tool_row = tk.Frame(self._run_scripts_frame, bg=P["bg1"])
        tool_row.pack(fill="x", pady=2)

        has_backup = rollback_available(self.graph["meta"]["root"])

        instr_btn = tk.Label(tool_row, text="⏱ Instrument", bg=P["bg3"],
                              fg=P["amber"], font=self._mono_xs,
                              padx=5, pady=2, cursor="hand2",
                              highlightbackground=P["line2"], highlightthickness=1)
        instr_btn.pack(side="left", padx=(0, 4))
        instr_btn.bind("<Button-1>", lambda _: self._open_instrument_dialog())

        if has_backup:
            rb_btn = tk.Label(tool_row, text="↩ Rollback", bg=P["bg3"],
                               fg=P["red"], font=self._mono_xs,
                               padx=5, pady=2, cursor="hand2",
                               highlightbackground=P["red"], highlightthickness=1)
            rb_btn.pack(side="left")
            rb_btn.bind("<Button-1>", lambda _: self._rollback_instrumentation())

    def _run_script(self, name: str, command: str):
        """Spawn a run script from side.project.json."""
        if not self.graph:
            return
        cwd = self.graph["meta"]["root"]
        if self._proc_mgr is None:
            self._proc_mgr = ProcessManager()

        proc = self._proc_mgr.start(name=name, command=command, cwd=cwd)
        self.processes[proc.id] = proc
        self._log.info("Started script '%s': %s  (pid %s)", name, command, proc.id)

        def _on_line(line, pid=proc.id):
            self.after(0, lambda: self._append_proc_log(pid, line, False))
        def _on_err(line, pid=proc.id):
            self.after(0, lambda: self._append_proc_log(pid, line, True))
        def _on_exit(code, pid=proc.id):
            self._log.info("Script '%s' exited with code %s", name, code)
            self.after(0, self._refresh_run_scripts)

        proc.on_stdout(_on_line)
        proc.on_stderr(_on_err)
        proc.on_exit(_on_exit)

        # Open proc panel and refresh run buttons
        self._toggle_proc_panel()
        self.after(200, self._refresh_run_scripts)


    def _run_sandbox(self, name: str, command: str, mode: str) -> None:
        """Run a script in a sandboxed temp copy of the project."""
        if not self.graph:
            return
        root = self.graph["meta"]["root"]
        if not hasattr(self, "_sandboxes"):
            self._sandboxes: dict = {}

        opts = SandboxOptions(mode=mode, keep_log_runs=3)
        sb   = SandboxRun(root, opts)
        self._log.info("Sandbox (%s) starting for '%s'", mode, name)
        self._show_loading(f"Preparing {mode} sandbox…")

        def _prepare_and_start():
            try:
                tmp = sb.prepare()
                self._log.info("Sandbox ready at %s", tmp)
                self.after(0, lambda: _do_start(tmp))
            except Exception as e:
                self._log.error("Sandbox prepare failed: %s", e)
                self.after(0, self._hide_loading)
                self.after(0, lambda m=str(e): messagebox.showerror(
                    "Sandbox Error", f"Prepare failed: {m}"))

        def _do_start(tmp):
            self._hide_loading()
            if self._proc_mgr is None:
                self._proc_mgr = ProcessManager()
            proc = sb.start(command, name=f"[{mode}] {name}")
            self.processes[proc.id] = proc
            sb_key = proc.id
            self._sandboxes[sb_key] = sb
            self._log.info("[%s] %s started in %s", mode, name, tmp)
            self._ensure_proc_monitor()

            def _on_line(line, pid=proc.id):
                self.after(0, lambda: self._append_proc_log(pid, line, False))
            def _on_err(line, pid=proc.id):
                self.after(0, lambda: self._append_proc_log(pid, line, True))
            def _on_exit(code, pid=proc.id, sk=sb_key):
                self._log.info("[%s] '%s' exited code %s", mode, name, code)
                sb2 = self._sandboxes.get(sk)
                if sb2:
                    log_dir = sb2.cleanup()
                    if log_dir:
                        self._log.info("Sandbox logs saved: %s", log_dir)
                    self._sandboxes.pop(sk, None)
                self.after(0, self._refresh_run_scripts)

            proc.on_stdout(_on_line)
            proc.on_stderr(_on_err)
            proc.on_exit(_on_exit)

            if not (self._proc_win and self._proc_win.winfo_exists()):
                self._build_proc_panel()
            self.after(200, self._refresh_run_scripts)

        threading.Thread(target=_prepare_and_start, daemon=True).start()

    def _open_instrument_dialog(self) -> None:
        """Open the instrument-project dialog window."""
        if not self.graph:
            messagebox.showinfo("Instrument", "Load a project first.")
            return
        root = self.graph["meta"]["root"]

        win = tk.Toplevel(self)
        win.title("S-IDE — Instrument Project")
        win.configure(bg=P["bg1"])
        win.geometry("480x440")
        win.resizable(True, True)
        win.transient(self)

        hdr = tk.Frame(win, bg=P["bg2"])
        hdr.pack(fill="x")
        tk.Frame(hdr, bg=P["line"], height=1).pack(fill="x")
        tk.Label(hdr, text="⏱ INSTRUMENT PROJECT", bg=P["bg2"], fg=P["amber"],
                 font=self._mono_l, padx=14, pady=8).pack(anchor="w")
        tk.Label(hdr,
                 text="Add @timed to public functions so S-IDE shows live timing on nodes.",
                 bg=P["bg2"], fg=P["t2"], font=self._mono_xs, padx=14, pady=4,
                 justify="left").pack(anchor="w")
        tk.Frame(hdr, bg=P["line"], height=1).pack(fill="x")

        body = tk.Frame(win, bg=P["bg1"])
        body.pack(fill="both", expand=True, padx=16, pady=10)

        public_var   = tk.BooleanVar(value=True)
        toplevel_var = tk.BooleanVar(value=True)
        tests_var    = tk.BooleanVar(value=False)
        backup_var   = tk.BooleanVar(value=True)
        preview_var  = tk.BooleanVar(value=False)

        for text, var, detail in [
            ("Public functions only",  public_var,   "(skip names starting with _)"),
            ("Top-level only",         toplevel_var, "(skip methods inside classes)"),
            ("Generate test stubs",    tests_var,    "(writes to test/ directory)"),
            ("Backup originals",       backup_var,   "(enables rollback later)"),
            ("Preview only (dry run)", preview_var,  "(no files are modified)"),
        ]:
            r = tk.Frame(body, bg=P["bg1"])
            r.pack(fill="x", pady=2)
            tk.Checkbutton(r, text=text, variable=var, bg=P["bg1"], fg=P["t1"],
                           selectcolor=P["bg3"], activebackground=P["bg1"],
                           font=self._mono_s).pack(side="left")
            tk.Label(r, text=detail, bg=P["bg1"], fg=P["t3"],
                     font=self._mono_xs).pack(side="left", padx=6)

        tk.Frame(body, bg=P["line"], height=1).pack(fill="x", pady=8)

        tk.Label(body, text="OUTPUT", bg=P["bg1"], fg=P["t3"],
                 font=self._mono_xs).pack(anchor="w")
        out_f = tk.Frame(body, bg=P["bg0"])
        out_f.pack(fill="both", expand=True)
        sb2 = tk.Scrollbar(out_f)
        sb2.pack(side="right", fill="y")
        out_text = tk.Text(out_f, bg=P["bg0"], fg=P["t1"], height=8,
                           font=(self._mono_xs.actual()["family"], 9),
                           yscrollcommand=sb2.set, bd=0, state="disabled", wrap="char")
        out_text.pack(fill="both", expand=True, pady=4)
        sb2.config(command=out_text.yview)
        out_text.tag_config("ok",   foreground=P["green"])
        out_text.tag_config("warn", foreground=P["amber"])
        out_text.tag_config("err",  foreground=P["red"])

        def _log_out(msg, level=""):
            tag = {"ok": "ok", "warn": "warn", "err": "err"}.get(level, "")
            out_text.config(state="normal")
            out_text.insert("end", msg + "\n", tag)
            out_text.see("end")
            out_text.config(state="disabled")

        def _run_instrument():
            opts = InstrumentOptions(
                public_only    = public_var.get(),
                top_level_only = toplevel_var.get(),
                add_tests      = tests_var.get(),
                backup         = backup_var.get(),
                preview        = preview_var.get(),
            )
            verb = "Previewing" if opts.preview else "Instrumenting"
            _log_out(f"{verb} {root}…", "warn")

            def _do():
                try:
                    result = Instrumenter(root, opts).run()
                    self.after(0, lambda r=result: _show_result(r))
                except Exception as e:
                    self.after(0, lambda m=str(e): _log_out(f"ERROR: {m}", "err"))
            threading.Thread(target=_do, daemon=True).start()

        def _show_result(result):
            _log_out(result.summary(), "ok" if not result.errors else "err")
            for f in result.files_modified[:20]:
                _log_out(f"  ✓ {f}", "ok")
            if len(result.files_modified) > 20:
                _log_out(f"  … and {len(result.files_modified)-20} more", "warn")
            for e in result.errors:
                _log_out(f"  ✗ {e}", "err")
            if result.tests_created:
                _log_out("\nTest stubs:", "warn")
                for t in result.tests_created:
                    _log_out(f"  {t}")
            if result.rollback_path and not result.preview:
                _log_out(f"\nBackup: {result.rollback_path}", "warn")
            self._refresh_run_scripts()

        tk.Frame(body, bg=P["line"], height=1).pack(fill="x", pady=(6, 4))
        btn_row = tk.Frame(body, bg=P["bg1"])
        btn_row.pack(fill="x")

        run_btn = tk.Label(btn_row, text="▶ Run", bg=P["green2"], fg=P["green"],
                           font=self._mono_s, padx=10, pady=4, cursor="hand2",
                           highlightbackground=P["green"], highlightthickness=1)
        run_btn.pack(side="left")
        run_btn.bind("<Button-1>", lambda _: _run_instrument())

        close_btn = tk.Label(btn_row, text="Close", bg=P["bg3"], fg=P["t2"],
                              font=self._mono_xs, padx=8, pady=4, cursor="hand2",
                              highlightbackground=P["line2"], highlightthickness=1)
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda _: win.destroy())

    def _rollback_instrumentation(self) -> None:
        """Rollback all instrumentation changes using the stored backup."""
        if not self.graph:
            return
        root = self.graph["meta"]["root"]
        if not messagebox.askyesno(
            "Rollback Instrumentation",
            "Restore all instrumented files to their original state?\n\n"
            "This will remove all @timed decorators that were added."
        ):
            return
        result = rollback(root)
        n = len(result["restored"])
        if result["errors"]:
            messagebox.showerror("Rollback", f"Restored {n} files.\nErrors: {result['errors']}")
        else:
            messagebox.showinfo("Rollback", f"Restored {n} files successfully.")
        self._refresh_run_scripts()

    def _build_version_panel(self):
        ver_frame = tk.Frame(self._sidebar, bg=P["bg1"])
        ver_frame.pack(fill="x")

        # Header toggle
        ver_hdr = tk.Frame(ver_frame, bg=P["bg1"], cursor="hand2")
        ver_hdr.pack(fill="x")
        tk.Label(ver_hdr, text="VERSIONS", bg=P["bg1"], fg=P["t3"],
                 font=self._mono_xs, padx=10, pady=6).pack(side="left")
        self._ver_chevron = tk.Label(ver_hdr, text="▸", bg=P["bg1"], fg=P["t3"],
                                      font=self._mono_xs, padx=8)
        self._ver_chevron.pack(side="right")

        # Body (hidden by default)
        self._ver_body = tk.Frame(ver_frame, bg=P["bg1"])

        btn_row = tk.Frame(self._ver_body, bg=P["bg1"])
        btn_row.pack(fill="x", padx=8, pady=(4, 2))

        archive_btn = tk.Label(btn_row, text="📦 Archive", bg=P["bg3"], fg=P["t2"],
                                font=self._mono_xs, padx=6, pady=3, cursor="hand2",
                                highlightbackground=P["line2"], highlightthickness=1)
        archive_btn.pack(side="left", fill="x", expand=True, padx=(0, 3))
        archive_btn.bind("<Button-1>", lambda _: self._archive_version())

        # Self-update row
        upd_self_row = tk.Frame(self._ver_body, bg=P["bg1"])
        upd_self_row.pack(fill="x", padx=8, pady=(0, 2))
        self_upd_btn = tk.Label(upd_self_row,
                                 text="⟳ Self-Update (~/Downloads/)",
                                 bg=P["bg3"], fg=P["cyan"],
                                 font=self._mono_xs, padx=6, pady=3, cursor="hand2",
                                 highlightbackground=P["line2"], highlightthickness=1)
        self_upd_btn.pack(side="left", fill="x", expand=True)
        self_upd_btn.bind("<Button-1>", lambda _: self._run_self_update())

        compress_btn = tk.Label(btn_row, text="🗜 Compress", bg=P["bg3"], fg=P["t2"],
                                 font=self._mono_xs, padx=6, pady=3, cursor="hand2",
                                 highlightbackground=P["line2"], highlightthickness=1)
        compress_btn.pack(side="left", fill="x", expand=True, padx=(3, 0))
        compress_btn.bind("<Button-1>", lambda _: self._compress_versions())

        # Update row
        upd_row = tk.Frame(self._ver_body, bg=P["bg1"])
        upd_row.pack(fill="x", padx=8, pady=2)

        upd_btn = tk.Label(upd_row, text="↑ Apply Update (.tar.gz)",
                            bg=P["bg3"], fg=P["t2"],
                            font=self._mono_xs, padx=6, pady=3, cursor="hand2",
                            highlightbackground=P["line2"], highlightthickness=1)
        upd_btn.pack(side="left", fill="x", expand=True, padx=(0, 3))
        upd_btn.bind("<Button-1>", lambda _: self._apply_update_dialog())

        self._bump_var = tk.StringVar(value="patch")
        bump_menu = ttk.Combobox(upd_row, textvariable=self._bump_var,
                                  values=["patch", "minor", "major"],
                                  width=6, font=self._mono_xs, state="readonly")
        bump_menu.pack(side="left", padx=(3, 0))

        # Version list
        self._ver_list_frame = tk.Frame(self._ver_body, bg=P["bg1"])
        self._ver_list_frame.pack(fill="x", padx=8, pady=(2, 6))

        self._ver_open = False

        def _toggle(_event=None):
            self._ver_open = not self._ver_open
            if self._ver_open:
                self._ver_body.pack(fill="x")
                self._ver_chevron.config(text="▾")
                self._refresh_version_list()
            else:
                self._ver_body.pack_forget()
                self._ver_chevron.config(text="▸")

        ver_hdr.bind("<Button-1>", _toggle)
        self._ver_chevron.bind("<Button-1>", _toggle)

    def _build_canvas(self, parent):
        canvas_frame = tk.Frame(parent, bg=P["bg0"])
        canvas_frame.grid(row=0, column=0, sticky="nsew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(canvas_frame, bg=P["bg0"],
                                  highlightthickness=0, cursor="fleur",
                                  takefocus=False)
        self._canvas.grid(row=0, column=0, sticky="nsew")

        # Zoom widget (overlaid, bottom-right)
        self._build_zoom_widget(canvas_frame)

        # Minimap (overlaid, bottom-right above zoom)
        self._minimap = tk.Canvas(canvas_frame, bg=P["bg1"],
                                   width=160, height=100,
                                   highlightbackground=P["line2"],
                                   highlightthickness=1)
        self._minimap.place(relx=1.0, rely=1.0, x=-12, y=-150, anchor="se")

        # Controls hint
        tk.Label(canvas_frame,
                 text="scroll→zoom · drag→pan · click→inspect · F→fit",
                 bg=P["bg0"], fg=P["t3"], font=self._mono_xs,
                 ).place(relx=0.0, rely=1.0, x=10, y=-8, anchor="sw")

        self._bind_canvas()

    def _build_zoom_widget(self, parent):
        zw = tk.Frame(parent, bg=P["bg0"])
        zw.place(relx=1.0, rely=1.0, x=-12, y=-258, anchor="se")

        def _zw_btn(text, cmd, padx=0, pady=1):
            b = tk.Label(zw, text=text, bg=P["bg2"], fg=P["t1"],
                         font=self._mono_s, width=3, pady=4,
                         cursor="hand2",
                         highlightbackground=P["line2"], highlightthickness=1)
            b.pack(fill="x", pady=pady)
            b.bind("<Button-1>", lambda _: cmd())
            b.bind("<Enter>", lambda _, w=b: w.config(bg=P["bg3"], fg=P["t0"]))
            b.bind("<Leave>", lambda _, w=b: w.config(bg=P["bg2"], fg=P["t1"]))
            return b

        _zw_btn("⊡", self._fit_view)
        tk.Frame(zw, bg=P["bg0"], height=4).pack()
        _zw_btn("+", lambda: self._zoom_by(1.25))
        self._zw_pct = tk.Label(zw, text="100%", bg=P["bg2"], fg=P["t3"],
                                 font=self._mono_xs, width=3, pady=3)
        self._zw_pct.pack(fill="x")
        _zw_btn("−", lambda: self._zoom_by(0.8))
        tk.Frame(zw, bg=P["bg0"], height=4).pack()
        _zw_btn("◁", lambda: self._pan_step(-120, 0))
        _zw_btn("△", lambda: self._pan_step(0, -120))
        _zw_btn("▽", lambda: self._pan_step(0, 120))
        _zw_btn("▷", lambda: self._pan_step(120, 0))

    def _build_inspector(self, parent):
        self._inspector = tk.Frame(parent, bg=P["bg1"], width=0)
        self._inspector.grid(row=0, column=1, sticky="ns")
        self._inspector.pack_propagate(False)
        self._inspector.grid_propagate(False)
        self._inspector_open = False
        self._inspector_width = 290

        self._insp_inner = tk.Frame(self._inspector, bg=P["bg1"],
                                     width=self._inspector_width)
        self._insp_inner.pack(fill="both", expand=True)

    def _build_statusbar(self):
        sb = tk.Frame(self, bg=P["bg0"], height=22)
        sb.grid(row=2, column=0, sticky="ew")
        sb.pack_propagate(False)

        tk.Frame(sb, bg=P["line"], height=1).pack(fill="x", side="top")

        inner = tk.Frame(sb, bg=P["bg0"])
        inner.pack(fill="both", expand=True, padx=12)

        # Left: language stats
        self._sb_langs_frame = tk.Frame(inner, bg=P["bg0"])
        self._sb_langs_frame.pack(side="left")

        tk.Frame(inner, bg=P["bg0"]).pack(side="left", fill="x", expand=True)

        # Right side — ordered right-to-left via side="right"

        # Connection indicator
        conn_frame = tk.Frame(inner, bg=P["bg0"])
        conn_frame.pack(side="right", padx=4)
        self._conn_dot = tk.Frame(conn_frame, bg=P["green"], width=6, height=6)
        self._conn_dot.pack(side="left", padx=(0, 4))
        tk.Label(conn_frame, text="local", bg=P["bg0"], fg=P["t2"],
                 font=self._mono_xs).pack(side="left")

        # Parse time + slowest stage
        self._sb_parsed_var = tk.StringVar(value="")
        tk.Label(inner, textvariable=self._sb_parsed_var,
                 bg=P["bg0"], fg=P["t3"],
                 font=self._mono_xs).pack(side="right", padx=(0, 8))

        # Live metrics badge — "⏱ 12 live" when .side-metrics.json is active
        self._metrics_badge_var = tk.StringVar(value="")
        self._metrics_badge = tk.Label(
            inner, textvariable=self._metrics_badge_var,
            bg=P["bg0"], fg=P["green"], font=self._mono_xs,
        )
        self._metrics_badge.pack(side="right", padx=(0, 6))

        # Self-monitoring badge — "◈ SELF" when S-IDE watches itself
        self._self_badge_var = tk.StringVar(value="")
        for node in self._vis_nodes():
            node_id = node.get("id")
            if not node_id: continue
            pos = node.get("position")
            if not pos: continue
            
            hx = (pos["x"] - self.vp_x) * self.vp_z + self.winfo_width() / 2
            hy = (pos["y"] - self.vp_y) * self.vp_z + self.winfo_height() / 2
        self._self_badge = tk.Label(
            inner, textvariable=self._self_badge_var,
            bg=P["bg0"], fg=P["green"], font=self._mono_xs,
        )
        self._self_badge.pack(side="right", padx=(0, 6))

        # Proc badge (running process count)
        self._proc_badge_var = tk.StringVar(value="")
        self._proc_badge = tk.Label(inner, textvariable=self._proc_badge_var,
                                     bg=P["bg0"], fg=P["green"],
                                     font=self._mono_xs)
        self._proc_badge.pack(side="right", padx=(8, 0))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "parse":
        target = sys.argv[2] if len(sys.argv) > 2 else "."
        print(f"Parsing project at {target}...")
        from parser.project_parser import parse_project
        parse_project(target)
        sys.exit(0)

    try:
        app = SIDE_App()
        app.mainloop()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
