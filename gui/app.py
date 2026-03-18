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
# Must happen before any s-ide package imports so that both
#   python gui/app.py          (run from project root)
#   python app.py              (run from gui/ directory)
# resolve correctly.
_GUI_DIR  = os.path.dirname(os.path.abspath(__file__))   # …/s-ide-py/gui
_ROOT_DIR = os.path.dirname(_GUI_DIR)                     # …/s-ide-py
for _p in (_ROOT_DIR, _GUI_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Now safe to import anything from the s-ide-py package tree
# Import workarounds to help some IDEs resolve local packages while maintaining script compatibility.
try:
    from .log import get_logger, get_log_path, recent_lines, clear_ring
    from .editor import EditorWindow
except (ImportError, ValueError):
    from gui.log import get_logger, get_log_path, recent_lines, clear_ring
    from gui.editor import EditorWindow

try:
    from ..monitor.perf import MetricsWatcher, ParseTimer, ProcessMonitor
    from ..monitor.instrumenter import rollback_available, rollback, Instrumenter, InstrumentOptions
    from ..ai.client import OllamaClient, ChatMessage as CM
    from ..ai.tools import TOOLS, dispatch_tool
    from ..ai.context import build_context, build_system_message
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
    from process.process_manager import ProcessManager
    from build.sandbox import SandboxRun, SandboxOptions
    from parser.project_parser import parse_project
    from version.version_manager import (
        archive_version, apply_update, list_versions, compress_loose as compress_versions
    )
    from build.packager import package_project, PackageOptions
    from parser.project_config import load_project_config, save_project_config, bump_version
    from build.cleaner import clean_project, CleanOptions


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


# ── Application ───────────────────────────────────────────────────────────────

class SIDE_App(tk.Tk):
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
        self.show_ext    = True
        self.filter_cat  = ""
        self.search_q    = ""

        # Drag/pan state
        self._drag: Optional[dict] = None   # {id, ox, oy, sx, sy}
        self._pan:  Optional[dict] = None   # {sx, sy, ox, oy}

        # AI panel state
        self._ai_model: str = "llama3.2"
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
        
        # State used by various panels/methods
        self.project_root: str = ""
        self._proc_list_canvas: Optional[tk.Canvas] = None
        self._proc_list_inner: Optional[tk.Frame] = None
        self._warnings_index: dict[str, list] = {}
        self._log_path: str = ""
        self._log_file: str = ""
        
        self._load_terminal_history()

        # ── Build UI ──────────────────────────────────────────────────────────
        self._build_ui()
        self._bind_keys()
        self._load_saved_projects()

        # Clean shutdown — stop all processes and monitor on window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)


    def _start_metrics_watcher(self, project_root: str) -> None:
        """Start (or restart) MetricsWatcher for the loaded project."""
        # Stop existing watcher
        if self._metrics_watcher:
            try:
                self._metrics_watcher.stop()
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
        nid = self._hit_test_node(event.x, event.y)
        if not nid:
            return
        node = self._node_map().get(nid)
        if node and not node.get('isExternal'):
            self._open_editor(node=node)

    def _canvas_right_click(self, event):
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

    # ── AI panel ─────────────────────────────────────────────────────────────

    def _toggle_ai_panel(self):
        self._select_bottom_tab("ai")

    def _build_ai_panel(self, parent):
        # Header
        hdr = tk.Frame(parent, bg=P['bg2'])
        hdr.pack(fill='x', side='top')
        tk.Frame(hdr, bg=P['line'], height=1).pack(fill='x')
        hi = tk.Frame(hdr, bg=P['bg2'])
        hi.pack(fill='x', padx=14, pady=8)
        tk.Label(hi, text='AI ASSISTANT', bg=P['bg2'], fg=P['t0'],
                 font=self._mono_l).pack(side='left')
        self._ai_status_var = tk.StringVar(value='checking Ollama...')
        tk.Label(hi, textvariable=self._ai_status_var, bg=P['bg2'],
                 fg=P['t2'], font=self._mono_xs).pack(side='left', padx=12)
        self._ai_model_var = tk.StringVar(value=self._ai_model)
        model_combo = ttk.Combobox(hi, textvariable=self._ai_model_var,
                                    width=18, font=self._mono_xs, state='readonly')
        model_combo.pack(side='right', padx=4)
        model_combo.bind('<<ComboboxSelected>>',
                          lambda _: setattr(self, '_ai_model', self._ai_model_var.get()))
        clear_btn = tk.Label(hi, text='Clear', bg=P['bg3'], fg=P['t2'],
                              font=self._mono_xs, padx=6, pady=2, cursor='hand2',
                              highlightbackground=P['line2'], highlightthickness=1)
        clear_btn.pack(side='right', padx=4)
        clear_btn.bind('<Button-1>', lambda _: self._ai_clear())
        tk.Frame(hdr, bg=P['line'], height=1).pack(fill='x')

        # Input (pack at bottom first so it stays visible)
        tk.Frame(parent, bg=P['line'], height=1).pack(fill='x', side='bottom')
        inp_f = tk.Frame(parent, bg=P['bg2'])
        inp_f.pack(fill='x', side='bottom', padx=10, pady=8)
        self._ai_input_var = tk.StringVar()
        self._ai_input = tk.Entry(inp_f, textvariable=self._ai_input_var,
                        bg=P['bg3'], fg=P['t0'], insertbackground=P['green'],
                        bd=0, font=(self._mono_xs.actual()['family'], 11), width=50)
        if self._ai_input:
            self._ai_input.pack(side='left', fill='x', expand=True, ipady=5, padx=(0, 8))
            self._ai_input.bind('<Return>', lambda _: self._ai_send())
            self._ai_input.focus_set()
        
        send_btn = tk.Label(inp_f, text='Send', bg=P['green2'], fg=P['green'],
                             font=self._mono_s, padx=10, pady=5, cursor='hand2',
                             highlightbackground=P['green'], highlightthickness=1)
        send_btn.pack(side='left')
        send_btn.bind('<Button-1>', lambda _: self._ai_send())

        # Conversation (takes remaining space in middle)
        co = tk.Frame(parent, bg=P['bg0'])
        co.pack(fill='both', expand=True, side='top')
        csb = tk.Scrollbar(co); csb.pack(side='right', fill='y')
        self._ai_conv = tk.Text(co, bg=P['bg0'], fg=P['t1'],
                                font=(self._mono_xs.actual()['family'], 10),
                                yscrollcommand=csb.set, bd=0, wrap='word',
                                state='disabled', padx=14, pady=8)
        if self._ai_conv:
            self._ai_conv.pack(fill='both', expand=True)
            csb.config(command=self._ai_conv.yview)
            self._ai_conv.tag_config('user',    foreground=P['cyan'])
            self._ai_conv.tag_config('ai',      foreground=P['t0'])
            self._ai_conv.tag_config('tool',    foreground=P['amber'])
            self._ai_conv.tag_config('error',   foreground=P['red'])
            self._ai_conv.tag_config('dim',     foreground=P['t2'])
            self._ai_conv.tag_config('thought', font=(self._mono_xs.actual()['family'], 9, 'italic'), foreground=P['t2'])
            self._ai_conv.tag_config('bold',    font=(self._mono_xs.actual()['family'], 10, 'bold'))
            self._ai_conv.tag_config('code',    font=(self._mono_xs.actual()['family'], 10), background=P['bg3'])
            self._ai_conv.tag_config('block',   font=(self._mono_xs.actual()['family'], 10), background=P['bg1'])
        self._ai_conv.tag_config('exec',    foreground=P['cyan'], font=(self._mono_xs.actual()['family'], 9, 'italic'))
        threading.Thread(target=lambda: self._ai_check_ollama(model_combo),
                         daemon=True).start()
        self._ai_append('Ask anything about the project. I can read files, search',
                         'dim')
        self._ai_append(' definitions, run tests, and check metrics.\n\n', 'dim')

    def _ai_check_ollama(self, model_combo):
        try:
            client = OllamaClient()
            if client.is_available():
                models = client.list_models()
                self._ai_available = True
                def _ok():
                    self._ai_status_var.set(f'{len(models)} model(s) available')
                    model_combo.config(values=models)
                    if models:
                        model_combo.set(models[0])
                        self._ai_model = models[0]
                    if self._ai_btn:
                        self._ai_btn.config(fg=P['green'])
                self.after(0, _ok)
            else:
                def _fail():
                    self._ai_status_var.set('Ollama not running  (ollama serve)')
                    if self._ai_btn:
                        self._ai_btn.config(fg=P['red'])
                self.after(0, _fail)
        except Exception as e:
            self.after(0, lambda: self._ai_status_var.set(f'Error: {e}'))

    def _ai_append(self, text, tag=''):
        w = getattr(self, '_ai_conv', None)
        if not w:
            return
        try:
            if not w.winfo_exists():
                return
            w.config(state='normal')
            w.insert('end', text, tag)
            w.see('end')
            w.config(state='disabled')
        except Exception:
            pass

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
        self._ai_reset_stream_state()
        
        focused = None
        if self.sel_nodes:
            nid = next(iter(self.sel_nodes))
            focused = self._node_map().get(nid)
        ctx = build_context(
            project_root=self.graph['meta']['root'] if self.graph else '',
            graph=self.graph,
            focused_node=focused,
            focused_file=focused.get('path', '') if focused else '',
        )

        def _on_chunk(text):
            self.after(0, lambda: self._ai_append_content(text))

        def run_ai():
            try:
                client = OllamaClient()
                if not self._ai_messages:
                    self._ai_messages.append(build_system_message(ctx))
                self._ai_messages.append(CM(role='user', content=prompt))
                
                res = client.chat_with_tools(
                    self._ai_model, 
                    self._ai_messages, 
                    TOOLS, 
                    lambda n, a: dispatch_tool(n, a, ctx),
                    on_text=_on_chunk
                )
                self._ai_messages.append(CM(role='assistant', content=res.content))
                # Auto-refresh plan if it changed
                self.after(0, lambda: self._refresh_plan())
            except Exception as e:
                self.after(0, lambda: self._ai_append(f'\nError: {e}\n', 'error'))

        threading.Thread(target=run_ai, daemon=True).start()

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
            btn.bind("<Button-1>", lambda _, k=cat_key: self._set_filter_cat(k))
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

        # PROC
        proc_btn = tk.Label(tb, text="⚡ PROC", bg=P["bg3"], fg=P["t2"],
                             font=self._mono_xs, padx=7, pady=3,
                             cursor="hand2",
                             highlightbackground=P["line2"], highlightthickness=1)
        proc_btn.grid(row=0, column=col[0], padx=(2, 2), pady=8)
        proc_btn.bind("<Button-1>", lambda _: self._toggle_proc_panel())
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
        self._term_tab   = add_tab("terminal", "Terminal")

        # Build inside the panels
        self._build_sidebar(self._proj_tab)
        self._build_ai_panel(self._ai_tab)
        self._build_plan_panel(self._plan_tab)
        self._build_playground_panel(self._play_tab)
        self._build_terminal_panel(self._term_tab)

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
            self._ai_input.focus_set()
        elif name == "terminal" and hasattr(self, "_term_input"):
            self._term_input.focus_set()
        elif name == "playground" and hasattr(self, "_play_text"):
            self._play_text.focus_set()

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
        self._term_input.bind("<Up>", lambda _: self._term_cycle_history(-1))
        self._term_input.bind("<Down>", lambda _: self._term_cycle_history(1))
        
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
        add_btn = tk.Label(hdr, text="+ PATH", bg=P["bg3"], fg=P["t2"],
                           font=self._mono_xs, padx=5, pady=2, cursor="hand2",
                           highlightbackground=P["line2"], highlightthickness=1)
        add_btn.pack(side="right")
        add_btn.bind("<Button-1>", lambda _: self._open_project_dialog())

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
                if self._run_body: self._run_body.pack(fill="x")
                if self._run_chevron: self._run_chevron.config(text="▾")
                self._refresh_run_scripts()
            else:
                if self._run_body: self._run_body.pack_forget()
                if self._run_chevron: self._run_chevron.config(text="▸")

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
                                  highlightthickness=0, cursor="fleur")
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

    # ── Canvas rendering ──────────────────────────────────────────────────────

    def _bind_canvas(self):
        c = self._canvas

        # Pan (middle mouse or left on empty canvas)
        c.bind("<ButtonPress-2>", self._pan_start)
        c.bind("<B2-Motion>", self._pan_move)
        c.bind("<ButtonRelease-2>", self._pan_end)

        # Left click — hit-test nodes/edges, else pan
        c.bind("<ButtonPress-1>", self._canvas_click)
        c.bind("<B1-Motion>", self._drag_move)
        c.bind("<ButtonRelease-1>", self._drag_end)

        # Hover — hit-test nodes and edges via Motion
        c.bind("<Motion>", self._canvas_motion)
        c.bind("<Double-Button-1>", self._canvas_double_click)
        c.bind("<Button-3>", self._canvas_right_click)

        # Scroll zoom
        c.bind("<MouseWheel>", self._on_scroll)        # Windows/macOS
        c.bind("<Button-4>", self._on_scroll)           # Linux scroll up
        c.bind("<Button-5>", self._on_scroll)           # Linux scroll down

        # Resize
        c.bind("<Configure>", self._on_resize)

    def _rebuild_hit_boxes(self) -> None:
        """
        Rebuild screen-space bounding boxes for all visible nodes.
        Called once after every full redraw, not on every motion event.
        Results are stored in self._hit_boxes for O(n) lookup per motion event.
        """
        boxes: dict = {}
        for node in self._vis_nodes():
            nid = node["id"]
            wx, wy = self._npos(nid)
            x0, y0 = self._w2s(wx, wy)
            x1 = x0 + NW * self.vp_z
            y1 = y0 + node_height(node) * self.vp_z
            boxes[nid] = (x0, y0, x1, y1)
        self._hit_boxes = boxes

    def _hit_test_node(self, sx: float, sy: float) -> str | None:
        """O(n) node hit-test using pre-built screen-space bounding boxes."""
        for nid, (x0, y0, x1, y1) in self._hit_boxes.items():
            if x0 <= sx <= x1 and y0 <= sy <= y1:
                return nid
        return None

    def _hit_test_edge(self, sx: float, sy: float) -> str | None:
        """
        Edge proximity test — only runs when no node is under the cursor,
        and uses a coarse bounding-box pre-filter to skip distant edges.
        """
        nm = self._node_map()
        best_id, best_dist = None, 8.0
        for edge in self._vis_edges():
            sn = nm.get(edge["source"])
            tn = nm.get(edge["target"])
            if not sn or not tn:
                continue
            # Coarse AABB filter — skip edges whose bbox doesn't contain cursor
            sx_w, sy_w = self._npos(edge["source"])
            tx_w, ty_w = self._npos(edge["target"])
            ex0, ey0 = self._w2s(min(sx_w, tx_w) - 10, min(sy_w, ty_w) - 10)
            ex1, ey1 = self._w2s(max(sx_w, tx_w) + NW + 10,
                                  max(sy_w, ty_w) + node_height(sn) + 10)
            if not (ex0 <= sx <= ex1 and ey0 <= sy <= ey1):
                continue
            # Fine bezier proximity check (fewer steps than before)
            x1s, y1s = self._w2s(sx_w + NW / 2, sy_w + node_height(sn))
            x2s, y2s = self._w2s(tx_w + NW / 2, ty_w)
            dy = abs(y2s - y1s)
            cp = max(40, dy * 0.45)
            pts = self._bezier_points(x1s, y1s, x1s, y1s + cp,
                                       x2s, y2s - cp, x2s, y2s, steps=6)
            for i in range(len(pts) - 1):
                ax, ay = (float(pts[i][0]), float(pts[i][1]))
                bx, by = (float(pts[i + 1][0]), float(pts[i + 1][1]))
                dx, dy2 = bx - ax, by - ay
                seg_len2 = dx * dx + dy2 * dy2
                if seg_len2 < 1.0:
                    continue
                t = max(0.0, min(1.0, ((float(sx) - float(ax)) * dx + (float(sy) - float(ay)) * dy2) / seg_len2))
                dist = math.hypot(float(sx) - (float(ax) + t * dx), float(sy) - (float(ay) + t * dy2))
                if dist < best_dist:
                    best_dist = dist
                    best_id = edge["id"]
        return best_id

    def _canvas_motion(self, event):
        """
        Hover handler — throttled to avoid triggering redraws on every pixel.
        Hit-test uses pre-built bounding boxes (O(n) dict lookup, not geometry).
        Edge hit-test only runs when no node is under cursor (AABB pre-filtered).
        """
        if self._drag or self._pan:
            return
        sx, sy = event.x, event.y
        new_hov_node = self._hit_test_node(sx, sy)
        new_hov_edge = None if new_hov_node else self._hit_test_edge(sx, sy)

        if new_hov_node != self.hov_node or new_hov_edge != self.hov_edge:
            self.hov_node = new_hov_node
            self.hov_edge = new_hov_edge
            cursor = "hand2" if (new_hov_node or new_hov_edge) else "fleur"
            self._canvas.config(cursor=cursor)
            # Use scheduled (coalesced) redraw for hover — 16ms throttle
            self._schedule_redraw()

    def _bind_keys(self):
        self.bind("<f>", lambda _: self._fit_view())
        self.bind("<F>", lambda _: self._fit_view())
        self.bind("<Escape>", self._clear_selection)

    def _on_resize(self, event):
        # Debounce resize — only redraw after resizing stops for 80ms
        if self._resize_after_id:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(80, self._redraw)

    def redraw(self):
        """Public redraw from background threads — coalesced via after()."""
        self._schedule_redraw()

    def _schedule_redraw(self) -> None:
        """Coalesce multiple redraw requests into one frame (~16ms throttle)."""
        if self._redraw_pending:
            return
        self._redraw_pending = True
        if self._redraw_after_id:
            self.after_cancel(self._redraw_after_id)
        self._redraw_after_id = self.after(16, self._do_redraw)

    def _redraw(self) -> None:
        """Immediate redraw — use for interactions that must not lag."""
        self._redraw_pending = False
        if self._redraw_after_id:
            self.after_cancel(self._redraw_after_id)
            self._redraw_after_id = None
        self._do_redraw()

    def _do_redraw(self) -> None:
        """Full canvas redraw with per-phase timing."""
        self._redraw_pending = False
        self._redraw_after_id = None
        t0 = time.monotonic()

        c = self._canvas
        c.delete("all")
        t_clear = time.monotonic()

        self._draw_grid()
        t_grid = time.monotonic()

        self._draw_edges()
        t_edges = time.monotonic()

        self._draw_nodes()
        t_nodes = time.monotonic()

        self._draw_minimap()
        t_mini = time.monotonic()

        # Update hit boxes after every full redraw
        self._rebuild_hit_boxes()

        total_ms = (time.monotonic() - t0) * 1000
        self._render_times.append({
            "ts":      time.time(),
            "total":   round(total_ms, 1),
            "clear":   round((t_clear - t0) * 1000, 1),
            "grid":    round((t_grid  - t_clear) * 1000, 1),
            "edges":   round((t_edges - t_grid)  * 1000, 1),
            "nodes":   round((t_nodes - t_edges) * 1000, 1),
            "minimap": round((t_mini  - t_nodes) * 1000, 1),
            "n_nodes": len(self._vis_nodes()),
            "n_edges": len(self._vis_edges()),
        })
        if len(self._render_times) > 120:
            self._render_times = self._render_times[-120:]

    # ── World ↔ screen coordinate transforms ─────────────────────────────────

    def _w2s(self, wx, wy):
        """World coords → screen coords."""
        return (wx * self.vp_z + self.vp_x,
                wy * self.vp_z + self.vp_y)

    def _s2w(self, sx, sy):
        """Screen coords → world coords."""
        return ((sx - self.vp_x) / self.vp_z,
                (sy - self.vp_y) / self.vp_z)

    # ── Grid ──────────────────────────────────────────────────────────────────

    def _draw_grid(self):
        c = self._canvas
        if not c:
            return
        W = float(c.winfo_width() or 1200)
        H = float(c.winfo_height() or 800)

        # Minor grid (20 units)
        minor = 20 * self.vp_z
        if minor > 6:
            ox = self.vp_x % minor
            oy = self.vp_y % minor
            x = float(ox)
            while x <= W:
                c.create_line(x, 0.0, x, H, fill=P["grid_minor"], width=1)
                x += minor
            y = oy
            while y <= H:
                c.create_line(0, y, W, y, fill=P["grid_minor"], width=1)
                y += minor

        # Major grid (100 units)
        major = 100 * self.vp_z
        if major > 10:
            ox = self.vp_x % major
            oy = self.vp_y % major
            x = ox
            while x <= W:
                c.create_line(x, 0, x, H, fill=P["grid_major"], width=1)
                x += major
            y = oy
            while y <= H:
                c.create_line(0, y, W, y, fill=P["grid_major"], width=1)
                y += major

    # ── Nodes ─────────────────────────────────────────────────────────────────

    def _invalidate_cache(self) -> None:
        """Call whenever graph, filters, or positions change."""
        self._cache_nodes = None
        self._cache_edges = None
        self._cache_node_map = None
        self._hit_boxes = {}

    def _vis_nodes(self) -> List[Any]:
        """Cached visible node list. Rebuilt only when cache is invalidated."""
        res: List[Any] = []
        cn = self._cache_nodes
        if cn is not None:
            # Use list() to ensure we return a new list of Any, not a potential None
            return list(cn)
        
        if not self.graph:
            self._cache_nodes = res
            return res
        
        nodes_raw = self.graph.get("nodes", [])
        nodes = list(nodes_raw) if isinstance(nodes_raw, list) else []
        # Virtual external nodes
        if self.show_ext:
            have = {n["id"] for n in nodes}
            seen: set = set()
            for e in self.graph.get("edges", []):
                if e.get("isExternal") and e["target"] not in have \
                        and e["target"] not in seen:
                    seen.add(e["target"])
                    nodes.append({
                        "id": e["target"],
                        "label": e.get("externalPackage", e["target"]),
                        "path": f"<ext>/{e.get('externalPackage','')}",
                        "category": "other", "ext": "",
                        "lines": 0, "size": 0, "tags": ["built-in"],
                        "imports": [], "exports": [], "definitions": [],
                        "errors": [], "isExternal": True,
                    })
        q = self.search_q.lower()
        self._cache_nodes = [
            n for n in nodes
            if (self.show_ext or not n.get("isExternal"))
            and (not self.filter_cat or n.get("category") == self.filter_cat)
            and (not q or q in n.get("label", "").lower()
                       or q in n.get("path", "").lower())
        ]
        return self._cache_nodes

    def _vis_edges(self) -> list:
        """Cached visible edge list. Rebuilt only when cache is invalidated."""
        if self._cache_edges is not None:
            return self._cache_edges
        if not self.graph:
            self._cache_edges = []
            return []
        ids = {n["id"] for n in self._vis_nodes()}
        self._cache_edges = [
            e for e in self.graph.get("edges", [])
            if e["source"] in ids and e["target"] in ids
        ]
        return self._cache_edges

    def _node_map(self) -> dict:
        """Cached {id: node} dict."""
        if self._cache_node_map is not None:
            return self._cache_node_map
        self._cache_node_map = {n["id"]: n for n in self._vis_nodes()}
        return self._cache_node_map

    def _npos(self, node_id: str):
        p = self.positions.get(node_id)
        if p is None:
            return (0.0, 0.0)
        return p

    def _draw_nodes(self):
        c = self._canvas
        nodes = self._vis_nodes()
        sel_edge_connected = set()
        for e in self._vis_edges():
            if e["id"] in self.sel_edges:
                sel_edge_connected.add(e["source"])
                sel_edge_connected.add(e["target"])

        for node in nodes:
            nid = node["id"]
            wx, wy = self._npos(nid)
            sx, sy = self._w2s(wx, wy)

            nh = node_height(node)
            sw = NW * self.vp_z
            sh = nh * self.vp_z

            # Determine visual state
            is_sel = nid in self.sel_nodes
            is_hov = nid == self.hov_node
            dimmed = (
                (self.sel_nodes and nid not in self.sel_nodes)
                or (self.sel_edges and nid not in sel_edge_connected)
            )

            fill, border, accent, _ = cat_style(node.get("category", "other"))

            alpha_mod = 0.15 if dimmed else 1.0
            brd_col = accent if is_sel else (border if not is_hov else P["line3"])

            # Card background
            c.create_rectangle(sx, sy, sx + sw, sy + sh,
                                fill=fill, outline=brd_col,
                                width=2 if is_sel else 1,
                                tags=("node", f"n:{nid}"))

            # Selection glow (extra rect)
            if is_sel:
                c.create_rectangle(sx - 2, sy - 2, sx + sw + 2, sy + sh + 2,
                                    outline=accent, width=1,
                                    fill="", tags=("node", f"n:{nid}"))

            # Header band
            hh = NH_HEADER * self.vp_z
            c.create_rectangle(sx, sy, sx + sw, sy + hh,
                                fill=border, outline="", tags=("node", f"n:{nid}"))

            if self.vp_z > 0.35:
                # Accent dot
                dot_r = 4 * self.vp_z
                c.create_oval(sx + 10 * self.vp_z, sy + hh / 2 - dot_r,
                              sx + 10 * self.vp_z + dot_r * 2,
                              sy + hh / 2 + dot_r,
                              fill=accent, outline="", tags=("node", f"n:{nid}"))

                # Label
                label_x = sx + 22 * self.vp_z
                label_y = sy + hh * 0.38
                font_sz = max(7, int(11 * self.vp_z))
                c.create_text(label_x, label_y,
                              text=node.get("label", ""),
                              anchor="sw", fill=P["t0"],
                              font=(self._mono.actual()["family"], font_sz, "bold"),
                              tags=("node", f"n:{nid}"))

                # Path
                path_short = node.get("path", "").replace(
                    node.get("label", ""), "").rstrip("/") or "."
                c.create_text(label_x, sy + hh * 0.72,
                              text=path_short[:28],
                              anchor="nw", fill=P["t2"],
                              font=(self._mono.actual()["family"], max(6, int(8 * self.vp_z))),
                              tags=("node", f"n:{nid}"))

                # Stats (top-right)
                if not node.get("isExternal"):
                    stats_x = sx + sw - 6 * self.vp_z
                    c.create_text(stats_x, sy + hh * 0.35,
                                  text=f"{node.get('lines', 0)}L",
                                  anchor="ne", fill=P["t2"],
                                  font=(self._mono.actual()["family"], max(6, int(8 * self.vp_z))),
                                  tags=("node", f"n:{nid}"))
                    c.create_text(stats_x, sy + hh * 0.68,
                                  text=fmt_size(node.get("size", 0)),
                                  anchor="ne", fill=P["t2"],
                                  font=(self._mono.actual()["family"], max(6, int(8 * self.vp_z))),
                                  tags=("node", f"n:{nid}"))

            if self.vp_z > 0.5:
                # Tags strip
                cy = sy + hh + 4 * self.vp_z
                tags = node.get("tags") or []
                tx = sx + 8 * self.vp_z
                for tag in tags[:5]:
                    tag_font_sz = max(6, int(8 * self.vp_z))
                    tw = len(tag) * tag_font_sz * 0.65 + 8
                    c.create_rectangle(tx, cy, tx + tw, cy + 14 * self.vp_z,
                                       fill=P["bg3"], outline=P["line3"], width=1,
                                       tags=("node", f"n:{nid}"))
                    c.create_text(tx + tw / 2, cy + 7 * self.vp_z,
                                  text=tag, anchor="center", fill=accent,
                                  font=(self._mono.actual()["family"], tag_font_sz),
                                  tags=("node", f"n:{nid}"))
                    tx += tw + 4 * self.vp_z
                if tags:
                    cy += 18 * self.vp_z

                # Definitions
                defs = (node.get("definitions") or [])[:MAX_DEFS]
                if defs:
                    cy += 4 * self.vp_z
                    c.create_text(sx + 10 * self.vp_z, cy,
                                  text="DEFS", anchor="nw",
                                  fill=P["t3"],
                                  font=(self._mono.actual()["family"], max(6, int(7 * self.vp_z))),
                                  tags=("node", f"n:{nid}"))
                    cy += 13 * self.vp_z
                    for d in defs:
                        icon = KIND_ICON.get(d.get("kind", ""), "·")
                        c.create_text(sx + 10 * self.vp_z, cy + 2 * self.vp_z,
                                      text=icon, anchor="nw", fill=accent,
                                      font=(self._mono.actual()["family"], max(7, int(9 * self.vp_z))),
                                      tags=("node", f"n:{nid}"))
                        c.create_text(sx + 22 * self.vp_z, cy + 2 * self.vp_z,
                                      text=d.get("name", "")[:22], anchor="nw",
                                      fill=P["t1"],
                                      font=(self._mono.actual()["family"], max(7, int(9 * self.vp_z))),
                                      tags=("node", f"n:{nid}"))
                        cy += NH_DEF_ROW * self.vp_z

            # Warning badge
            warns = self._node_warnings(node["id"])
            if warns:
                bx = sx + sw - 5 * self.vp_z
                by = sy - 5 * self.vp_z
                br = 7 * self.vp_z
                c.create_oval(bx - br, by - br, bx + br, by + br,
                              fill=P["amber"], outline=P["bg0"],
                              tags=("node", f"n:{nid}"))
                if len(warns) > 1:
                    c.create_text(bx, by, text=str(len(warns)),
                                  fill="#000", anchor="center",
                                  font=(self._mono.actual()["family"], max(6, int(7 * self.vp_z)), "bold"),
                                  tags=("node", f"n:{nid}"))

            # Port connectors (top + bottom)
            port_sz = 4 * self.vp_z
            cx_port = sx + sw / 2
            # In port
            c.create_oval(cx_port - port_sz, sy - port_sz,
                          cx_port + port_sz, sy + port_sz,
                          fill=accent, outline=fill,
                          tags=("node", f"n:{nid}"))
            # Out port
            c.create_oval(cx_port - port_sz, sy + sh - port_sz,
                          cx_port + port_sz, sy + sh + port_sz,
                          fill=accent, outline=fill,
                          tags=("node", f"n:{nid}"))

            # ── Live timing overlay ────────────────────────────────────────────
            # Show per-file metrics from .side-metrics.json if available
            m = self._node_metrics(node.get("path", ""))
            if m and self.vp_z > 0.3:
                avg_ms   = m.get("avg_ms", 0.0)
                calls    = m.get("calls", 0)
                last_ms  = m.get("last_ms", 0.0)
                age      = time.time() - m.get("last_ts", 0.0)
                # Colour by recency and speed: green=fast+recent, amber=slow, red=very slow
                if age > 8:
                    metric_col = P["t3"]      # stale — grey
                elif avg_ms < 10:
                    metric_col = P["green"]
                elif avg_ms < 100:
                    metric_col = P["amber"]
                else:
                    metric_col = P["red"]

                # Timing strip along the bottom of the card
                strip_h = max(3, int(4 * self.vp_z))
                # Background strip
                c.create_rectangle(sx, sy + sh - strip_h,
                                   sx + sw, sy + sh,
                                   fill=P["bg0"], outline="",
                                   tags=("node", f"n:{nid}"))
                # Fill proportional to avg_ms (capped at 500ms = full width)
                fill_frac = min(1.0, avg_ms / 500.0)
                if fill_frac > 0:
                    c.create_rectangle(sx, sy + sh - strip_h,
                                       sx + fill_frac * sw, sy + sh,
                                       fill=metric_col, outline="",
                                       tags=("node", f"n:{nid}"))

                # Text badge: "42× 28ms"
                if self.vp_z > 0.5:
                    badge_text = f"{calls}×  {avg_ms:.0f}ms avg"
                    font_sz = max(7, int(8 * self.vp_z))
                    # Small backing rect for readability
                    c.create_text(
                        sx + sw - 4 * self.vp_z,
                        sy + sh - strip_h - 3 * self.vp_z,
                        text=badge_text,
                        anchor="se",
                        fill=metric_col,
                        font=(self._mono_xs.actual()["family"], font_sz),
                        tags=("node", f"n:{nid}"),
                    )

            # Dimming overlay
            if dimmed:
                c.create_rectangle(sx, sy, sx + sw, sy + sh,
                                   fill=P["bg0"], outline="",
                                   stipple="gray75",
                                   tags=("node", f"n:{nid}"))

        # Node events are handled by canvas-level bindings in _bind_canvas()
        # (tag_bind inside draw loops causes handler accumulation across redraws)

    # ── Edges ─────────────────────────────────────────────────────────────────

    def _draw_edges(self):
        c = self._canvas
        node_map = {n["id"]: n for n in self._vis_nodes()}
        edges = self._vis_edges()

        sel_connected = set()
        for e in edges:
            if e["id"] in self.sel_edges:
                sel_connected.add(e["source"])
                sel_connected.add(e["target"])

        for edge in edges:
            sn = node_map.get(edge["source"])
            tn = node_map.get(edge["target"])
            if not sn or not tn:
                continue

            sx_w, sy_w = self._npos(edge["source"])
            tx_w, ty_w = self._npos(edge["target"])
            snh = node_height(sn)
            tnh = node_height(tn)

            # World-space control points (bottom of source, top of target)
            x1w = sx_w + NW / 2
            y1w = sy_w + snh
            x2w = tx_w + NW / 2
            y2w = ty_w

            x1, y1 = self._w2s(x1w, y1w)
            x2, y2 = self._w2s(x2w, y2w)

            # Bezier control handles
            dy = abs(y2 - y1)
            cp = max(40, dy * 0.45)
            cx1, cy1 = x1, y1 + cp
            cx2, cy2 = x2, y2 - cp

            color, _ = edge_style(edge.get("type", ""))

            is_sel = edge["id"] in self.sel_edges
            is_hov = edge["id"] == self.hov_edge
            is_conn = (edge["source"] in self.sel_nodes
                       or edge["target"] in self.sel_nodes)

            if self.sel_nodes:
                opacity_full = is_conn
            elif self.sel_edges:
                opacity_full = is_sel
            else:
                opacity_full = True

            if not opacity_full and not is_hov:
                # Draw as very dim
                draw_color = P["t3"]
                width = 1
            else:
                draw_color = color
                width = 3 if (is_sel or is_hov) else (2 if is_conn else 1)

            # Approximate cubic bezier with line segments
            pts = self._bezier_points(x1, y1, cx1, cy1, cx2, cy2, x2, y2, steps=16)
            flat = [v for pt in pts for v in pt]
            if len(flat) >= 4:
                c.create_line(*flat, fill=draw_color, width=width,
                              smooth=True, tags=("edge", f"e:{edge['id']}"))

            # Arrow head
            if len(pts) >= 2:
                ax, ay = pts[-1]
                bx, by = pts[-2]
                self._draw_arrowhead(c, bx, by, ax, ay, draw_color, width,
                                     f"e:{edge['id']}")

            # Symbol label on hover
            if is_hov and edge.get("symbols"):
                mx = (x1 + x2) / 2
                my = (y1 + y2) / 2 - 10
                syms = edge["symbols"][:3]
                label = ", ".join(syms)
                if len(edge["symbols"]) > 3:
                    label += "…"
                c.create_text(mx, my, text=label, fill=draw_color,
                              font=(self._mono.actual()["family"], max(7, int(9 * self.vp_z))),
                              tags=("edge", f"e:{edge['id']}"))

        # Bind edge events (use a wide invisible hit line)
        for edge in edges:
            sn = node_map.get(edge["source"])
            tn = node_map.get(edge["target"])
            if not sn or not tn:
                continue
            sx_w, sy_w = self._npos(edge["source"])
            tx_w, ty_w = self._npos(edge["target"])
            x1, y1 = self._w2s(sx_w + NW / 2, sy_w + node_height(sn))
            x2, y2 = self._w2s(tx_w + NW / 2, ty_w)
            dy = abs(y2 - y1)
            cp = max(40, dy * 0.45)
            pts = self._bezier_points(x1, y1, x1, y1 + cp, x2, y2 - cp, x2, y2, steps=12)
            flat = [v for pt in pts for v in pt]
            if len(flat) >= 4:
                hit = c.create_line(*flat, fill="", width=14, smooth=True,
                                    tags=("edgehit", f"eh:{edge['id']}"))
            # Edge events handled by canvas-level Motion/Click bindings

    @staticmethod
    def _bezier_points(x0, y0, cx0, cy0, cx1, cy1, x1, y1, steps=16):
        pts = []
        for i in range(steps + 1):
            t = i / steps
            u = 1 - t
            x = u**3 * x0 + 3*u**2*t * cx0 + 3*u*t**2 * cx1 + t**3 * x1
            y = u**3 * y0 + 3*u**2*t * cy0 + 3*u*t**2 * cy1 + t**3 * y1
            pts.append((x, y))
        return pts

    @staticmethod
    def _draw_arrowhead(canvas, x1, y1, x2, y2, color, width, tag):
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1:
            return
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        size = max(6, width * 3)
        p1 = (x2, y2)
        p2 = (x2 - ux * size + px * size * 0.4,
              y2 - uy * size + py * size * 0.4)
        p3 = (x2 - ux * size - px * size * 0.4,
              y2 - uy * size - py * size * 0.4)
        canvas.create_polygon(p1, p2, p3, fill=color, outline="", tags=tag)

    # ── Minimap ───────────────────────────────────────────────────────────────

    def _draw_minimap(self):
        mm = self._minimap
        mm.delete("all")
        nodes = self._vis_nodes()
        if not nodes:
            return

        W = mm.winfo_width()  or 160
        H = mm.winfo_height() or 100

        xs = [self._npos(n["id"])[0] for n in nodes]
        ys = [self._npos(n["id"])[1] for n in nodes]
        x0 = min(xs) - 20
        x1 = max(xs) + NW + 20
        y0 = min(ys) - 20
        y1 = max(ys) + 200

        rng_x = x1 - x0 or 1
        rng_y = y1 - y0 or 1
        sc = min(W / rng_x, H / rng_y) * 0.88
        mx = (W - rng_x * sc) / 2
        my = (H - rng_y * sc) / 2

        def mm_pt(wx, wy):
            return ((wx - x0) * sc + mx, (wy - y0) * sc + my)

        # Edges
        for e in self._vis_edges():
            sn = next((n for n in nodes if n["id"] == e["source"]), None)
            tn = next((n for n in nodes if n["id"] == e["target"]), None)
            if not sn or not tn:
                continue
            ax, ay = mm_pt(*self._npos(e["source"]))
            bx, by = mm_pt(*self._npos(e["target"]))
            mm.create_line(ax, ay, bx, by, fill=P["line3"], width=0.5)

        # Nodes
        for n in nodes:
            wx, wy = self._npos(n["id"])
            nx, ny = mm_pt(wx, wy)
            nw = NW * sc
            nh = node_height(n) * sc
            _, border, accent, _ = cat_style(n.get("category", "other"))
            is_sel = n["id"] in self.sel_nodes
            mm.create_rectangle(nx, ny, nx + nw, ny + nh,
                                 fill=P["bg2"],
                                 outline=accent if is_sel else border,
                                 width=1.5 if is_sel else 0.5)

        # Viewport rect
        cw = self._canvas.winfo_width()  or 1200
        ch = self._canvas.winfo_height() or 800
        vx1, vy1 = mm_pt(-self.vp_x / self.vp_z, -self.vp_y / self.vp_z)
        vx2, vy2 = mm_pt((-self.vp_x + cw) / self.vp_z,
                         (-self.vp_y + ch) / self.vp_z)
        mm.create_rectangle(vx1, vy1, vx2, vy2,
                             outline=P["green"], fill="", width=1)

    # ── Viewport ──────────────────────────────────────────────────────────────

    def _apply_vp(self):
        """Update viewport — invalidates hit boxes and schedules a redraw."""
        pct = f"{int(self.vp_z * 100)}%"
        self._zoom_var.set(pct)
        self._zw_pct.config(text=pct)
        # Invalidate hit boxes — positions in screen space changed
        self._hit_boxes = {}
        self._schedule_redraw()

    def _zoom_by(self, factor, cx=None, cy=None):
        cw = self._canvas.winfo_width()  or 1200
        ch = self._canvas.winfo_height() or 800
        if cx is None:
            cx = cw / 2
        if cy is None:
            cy = ch / 2
        nz = max(0.08, min(4.0, self.vp_z * factor))
        self.vp_x = cx - (cx - self.vp_x) * (nz / self.vp_z)
        self.vp_y = cy - (cy - self.vp_y) * (nz / self.vp_z)
        self.vp_z = nz
        self._apply_vp()

    def _pan_step(self, dx, dy):
        self.vp_x += dx
        self.vp_y += dy
        self._apply_vp()

    def _fit_view(self):
        nodes = self._vis_nodes()
        if not nodes:
            return
        xs = [self._npos(n["id"])[0] for n in nodes]
        ys = [self._npos(n["id"])[1] for n in nodes]
        x0 = min(xs) - 60
        x1 = max(xs) + NW + 60
        y0 = min(ys) - 60
        y1 = max(ys) + 300
        cw = self._canvas.winfo_width()  or 1200
        ch = self._canvas.winfo_height() or 800
        if cw < 10 or ch < 10:
            return
        self.vp_z = min(cw / (x1 - x0), ch / (y1 - y0), 1.4) * 0.88
        self.vp_x = -x0 * self.vp_z + (cw - (x1 - x0) * self.vp_z) / 2
        self.vp_y = -y0 * self.vp_z + (ch - (y1 - y0) * self.vp_z) / 2
        self._apply_vp()

    # ── Input events ──────────────────────────────────────────────────────────

    def _on_scroll(self, event):
        if event.num == 4 or (hasattr(event, "delta") and event.delta > 0):
            factor = 1.1
        else:
            factor = 0.9
        self._zoom_by(factor, event.x, event.y)

    def _canvas_click(self, event):
        sx, sy = event.x, event.y
        # Hit-test node first
        nid = self._hit_test_node(sx, sy)
        if nid:
            self._node_click(event, nid)
            return
        # Then edge
        eid = self._hit_test_edge(sx, sy)
        if eid:
            self._edge_click(event, eid)
            return
        # Hit nothing — clear selection, start pan
        self.sel_nodes.clear()
        self.sel_edges.clear()
        self._close_inspector()
        self._pan = {"sx": sx, "sy": sy, "ox": self.vp_x, "oy": self.vp_y}
        self._canvas.config(cursor="fleur")
        self._redraw()

    def _node_click(self, event, node_id):
        self.sel_nodes = {node_id}
        self.sel_edges.clear()
        node = next((n for n in self._vis_nodes() if n["id"] == node_id), None)
        if node:
            self._inspect_node(node)
        wx, wy = self._npos(node_id)
        self._drag = {
            "id": node_id,
            "ox": wx, "oy": wy,
            "sx": event.x, "sy": event.y,
        }
        self._redraw()

    def _drag_move(self, event):
        if self._drag:
            d = self._drag
            dwx = (event.x - d["sx"]) / self.vp_z
            dwy = (event.y - d["sy"]) / self.vp_z
            self.positions[d["id"]] = (d["ox"] + dwx, d["oy"] + dwy)
            # Invalidate hit boxes — dragged node moved in screen space
            self._hit_boxes = {}
            # Partial redraw: just edges + nodes (skip grid for perf)
            self._canvas.delete("edge")
            self._canvas.delete("edgehit")
            self._canvas.delete("node")
            self._draw_edges()
            self._draw_nodes()
            self._draw_minimap()
            self._rebuild_hit_boxes()
        elif self._pan:
            p = self._pan
            self.vp_x = p["ox"] + (event.x - p["sx"])
            self.vp_y = p["oy"] + (event.y - p["sy"])
            self._apply_vp()

    def _drag_end(self, event):
        self._drag = None
        self._pan = None
        self._canvas.config(cursor="fleur")

    def _pan_start(self, event):
        self._pan = {"sx": event.x, "sy": event.y,
                     "ox": self.vp_x, "oy": self.vp_y}
        self._canvas.config(cursor="fleur")

    def _pan_move(self, event):
        if self._pan:
            self.vp_x = self._pan["ox"] + (event.x - self._pan["sx"])
            self.vp_y = self._pan["oy"] + (event.y - self._pan["sy"])
            self._apply_vp()

    def _pan_end(self, event):
        self._pan = None
        self._canvas.config(cursor="fleur")

    # Hover state is now managed by _canvas_motion via hit-testing.
    # These stubs remain in case any code path still calls them.
    def _node_enter(self, node_id): pass
    def _node_leave(self, node_id): pass
    def _edge_enter(self, edge_id): pass
    def _edge_leave(self, edge_id): pass

    def _edge_click(self, event, edge_id):
        self.sel_edges = {edge_id}
        self.sel_nodes.clear()
        edge = next((e for e in self._vis_edges() if e["id"] == edge_id), None)
        if edge:
            self._inspect_edge(edge)
        self._redraw()

    def _clear_selection(self, event=None):
        self.sel_nodes.clear()
        self.sel_edges.clear()
        self._close_inspector()
        self._redraw()

    # ── Filter / search ───────────────────────────────────────────────────────

    def _set_filter_cat(self, cat_key):
        self.filter_cat = cat_key
        self._update_cat_chips()
        self._invalidate_cache()
        self._redraw()

    def _update_cat_chips(self):
        for key, btn in self._cat_btns.items():
            active = (key == self.filter_cat)
            btn.config(
                bg=P["bg4"] if active else P["bg3"],
                fg=P["green"] if active else P["t2"],
                highlightbackground=P["green2"] if active else P["line2"],
            )

    def _toggle_ext(self, event=None):
        self.show_ext = not self.show_ext
        self._ext_btn.config(
            fg=P["green"] if self.show_ext else P["t2"],
            highlightbackground=P["green2"] if self.show_ext else P["line2"],
        )
        self._invalidate_cache()
        self._redraw()

    def _on_search(self, *args):
        self.search_q = self._search_var.get()
        self._invalidate_cache()
        self._redraw()

    # ── Project management ────────────────────────────────────────────────────

    def _load_saved_projects(self):
        """Try to load projects from a config file."""
        config_path = os.path.join(os.path.expanduser("~"), ".s-ide-projects.json")
        if os.path.exists(config_path):
            try:
                with open(config_path) as f:
                    self.projects = json.load(f)
                self._render_project_list()
            except Exception:
                pass

    def _save_project_list(self):
        config_path = os.path.join(os.path.expanduser("~"), ".s-ide-projects.json")
        try:
            with open(config_path, "w") as f:
                json.dump(self.projects, f, indent=2)
        except Exception:
            pass

    def _open_project_dialog(self):
        path = filedialog.askdirectory(title="Open Project Folder")
        if path:
            self._load_project(path)

    def _load_project(self, path: str):
        """Parse a project in a background thread, then update UI."""
        path = os.path.abspath(path)
        self._log.info("Loading project: %s", path)
        self._show_loading(f"Parsing {os.path.basename(path)}…")

        def _parse():
            try:
                self._log.debug("parse_project thread started")
                graph = parse_project(path, save_json=True)
                gdict = graph.to_dict()
                # Embed per-stage perf data into the dict
                if hasattr(graph, "_perf"):
                    gdict["meta"]["perf"] = graph._perf
                n_nodes = gdict["meta"]["totalFiles"]
                n_edges = gdict["meta"]["totalEdges"]
                ms      = gdict["meta"]["parseTime"]
                perf    = gdict["meta"].get("perf", {})
                slowest = perf.get("slowest", "")
                self._log.info(
                    "Parse OK — %d nodes, %d edges in %dms%s",
                    n_nodes, n_edges, ms,
                    f"  (slowest: {slowest})" if slowest else "",
                )
                self.after(0, lambda: self._apply_graph(gdict, path))
            except Exception as exc:
                import traceback
                self._log.error("Parse failed: %s\n%s", exc, traceback.format_exc())
                self.after(0, lambda: self._on_parse_error(str(exc)))

        threading.Thread(target=_parse, daemon=True).start()

    def _apply_graph(self, gdict: dict, path: str):
        self.graph = gdict
        self.positions.clear()
        self._invalidate_cache()
        # Pre-build warning index: node_id → [warning dicts]
        self._warnings_index: dict = {}
        for w in gdict.get("meta", {}).get("docs", {}).get("warnings", []):
            for nid in w.get("affectedFiles") or []:
                self._warnings_index.setdefault(nid, []).append(w)

        # Seed positions from layout engine output
        for node in gdict.get("nodes", []):
            pos = node.get("position")
            if pos:
                self.positions[node["id"]] = (pos["x"], pos["y"])

        # External virtual nodes
        have = {n["id"] for n in gdict.get("nodes", [])}
        ey = -300.0
        for e in gdict.get("edges", []):
            if e.get("isExternal") and e["target"] not in have \
                    and e["target"] not in self.positions:
                self.positions[e["target"]] = (1400.0, ey)
                ey += 180.0

        # Update project list
        name = gdict["meta"]["project"]["name"]
        if not any(p["path"] == path for p in self.projects):
            self.projects.insert(0, {"name": name, "path": path})
            self._save_project_list()

        # Self-detection: is S-IDE looking at itself?
        self._is_self = (
            os.path.normcase(os.path.abspath(gdict["meta"]["root"])) ==
            os.path.normcase(os.path.abspath(_ROOT_DIR))
        )
        display_name = ("◈ " + name) if self._is_self else name
        self._lbl_project.config(
            text=display_name,
            fg=P["green"] if self._is_self else P["t1"],
        )
        if self._is_self:
            self._log.info("S-IDE is monitoring itself: %s", _ROOT_DIR)

        self.project_root = path
        # Start/restart MetricsWatcher for the new project
        self._start_metrics_watcher(gdict["meta"]["root"])
        self._render_project_list()
        self._update_status_bar()
        self._update_doc_badge()
        # Refresh run panel if it's open
        if self._run_open:
            self._refresh_run_scripts()
        # Refresh perf panel if build window is open
        if self._build_win and self._build_win.winfo_exists():
            if hasattr(self, "_perf_frame"):
                self._refresh_perf_display()
        self._hide_loading()

        self.after(50, self._fit_view)   # fit after canvas has sized

    def _on_parse_error(self, msg: str):
        self._hide_loading()
        messagebox.showerror("Parse Error", msg)

    def _render_project_list(self):
        for w in self._proj_list_frame.winfo_children():
            w.destroy()

        active_path = self.graph["meta"]["root"] if self.graph else None

        for p in self.projects:
            is_active = (active_path == p["path"])
            row = tk.Frame(self._proj_list_frame,
                           bg=P["bg3"] if is_active else P["bg1"],
                           cursor="hand2")
            row.pack(fill="x")

            dot = tk.Frame(row, bg=P["green"] if is_active else P["t3"],
                           width=6, height=6)
            dot.pack(side="left", padx=(12, 6), pady=8)

            tk.Label(row, text=p["name"][:22], bg=row["bg"],
                     fg=P["t0"] if is_active else P["t2"],
                     font=self._mono_s, anchor="w").pack(side="left", fill="x", expand=True)

            del_btn = tk.Label(row, text="✕", bg=row["bg"],
                               fg=P["t3"], font=self._mono_xs,
                               padx=8, cursor="hand2")
            del_btn.pack(side="right")
            del_btn.bind("<Button-1>",
                         lambda _, pp=p["path"]: self._remove_project(pp))

            row.bind("<Button-1>",
                     lambda _, pp=p["path"]: self._load_project(pp))
            for child in row.winfo_children():
                if child != del_btn:
                    child.bind("<Button-1>",
                               lambda _, pp=p["path"]: self._load_project(pp))

    def _remove_project(self, path: str):
        self.projects = [p for p in self.projects if p["path"] != path]
        self._save_project_list()
        self._render_project_list()

    # ── Inspector ─────────────────────────────────────────────────────────────

    def _open_inspector(self, build_fn):
        """Open the inspector panel and populate it with build_fn()."""
        # Clear
        for w in self._insp_inner.winfo_children():
            w.destroy()

        build_fn(self._insp_inner)

        if not self._inspector_open:
            self._inspector.config(width=self._inspector_width)
            self._inspector_open = True

    def _close_inspector(self):
        if self._inspector_open:
            self._inspector.config(width=0)
            self._inspector_open = False

    def _insp_header(self, parent, title: str, subtitle: str, accent: str):
        hdr = tk.Frame(parent, bg=P["bg2"])
        hdr.pack(fill="x")
        tk.Frame(hdr, bg=P["line"], height=1).pack(fill="x")
        inner = tk.Frame(hdr, bg=P["bg2"])
        inner.pack(fill="x", padx=12, pady=10)
        tk.Label(inner, text=title, bg=P["bg2"], fg=accent,
                 font=self._mono_l, anchor="w", wraplength=220).pack(side="left", fill="x", expand=True)
        close = tk.Label(inner, text="✕", bg=P["bg2"], fg=P["t3"],
                         font=self._mono_s, cursor="hand2", padx=4)
        close.pack(side="right")
        close.bind("<Button-1>", lambda _: self._close_inspector())
        if subtitle:
            tk.Label(hdr, text=subtitle, bg=P["bg2"], fg=P["t2"],
                     font=self._mono_xs, anchor="w", padx=12,
                     wraplength=250).pack(fill="x")
        tk.Frame(hdr, bg=P["line"], height=1).pack(fill="x")

    def _insp_section(self, parent, title: str):
        tk.Label(parent, text=title, bg=P["bg1"], fg=P["t3"],
                 font=self._mono_xs, anchor="w", padx=12,
                 pady=4).pack(fill="x")

    def _insp_row(self, parent, icon: str, text: str, accent: str, detail=""):
        row = tk.Frame(parent, bg=P["bg1"])
        row.pack(fill="x", padx=12, pady=1)
        tk.Label(row, text=icon, bg=P["bg1"], fg=accent,
                 font=self._mono_s, width=2).pack(side="left")
        tk.Label(row, text=text[:30], bg=P["bg1"], fg=P["t1"],
                 font=self._mono_s, anchor="w").pack(side="left", fill="x", expand=True)
        if detail:
            tk.Label(row, text=detail, bg=P["bg1"], fg=P["t3"],
                     font=self._mono_xs).pack(side="right", padx=(0, 4))
        tk.Frame(parent, bg=P["line"], height=1).pack(fill="x", padx=12)

    def _inspect_node(self, node: dict):
        _, _, accent, _ = cat_style(node.get("category", "other"))
        edges = self._vis_edges()
        node_map = {n["id"]: n for n in self._vis_nodes()}
        in_edges  = [e for e in edges if e["target"] == node["id"]]
        out_edges = [e for e in edges if e["source"] == node["id"]]
        warns = self._node_warnings(node["id"])

        def build(parent):
            self._insp_header(parent, node.get("label", ""), node.get("path", ""), accent)
            body = tk.Frame(parent, bg=P["bg1"])
            body.pack(fill="both", expand=True)
            scroll = tk.Scrollbar(body, orient="vertical")
            scroll.pack(side="right", fill="y")
            canvas_scroll = tk.Canvas(body, bg=P["bg1"],
                                       yscrollcommand=scroll.set,
                                       highlightthickness=0)
            canvas_scroll.pack(side="left", fill="both", expand=True)
            scroll.config(command=canvas_scroll.yview)
            inner = tk.Frame(canvas_scroll, bg=P["bg1"])
            canvas_scroll.create_window((0, 0), window=inner, anchor="nw", width=self._inspector_width - 4)

            def on_configure(e):
                canvas_scroll.configure(scrollregion=canvas_scroll.bbox("all"))
            inner.bind("<Configure>", on_configure)

            # Stats grid
            stats = [
                ("LINES", str(node.get("lines", 0)), accent),
                ("SIZE",  fmt_size(node.get("size", 0)), accent),
                ("IMPORTS", str(len(node.get("imports") or [])), P["blue"]),
                ("EXPORTS", str(len(node.get("exports") or [])), P["purple"]),
            ]
            grid = tk.Frame(inner, bg=P["bg1"])
            grid.pack(fill="x", padx=10, pady=8)
            for i, (lbl, val, col) in enumerate(stats):
                cell = tk.Frame(grid, bg=P["bg2"],
                                highlightbackground=P["line"], highlightthickness=1)
                cell.grid(row=i // 2, column=i % 2, padx=3, pady=3, sticky="ew")
                grid.columnconfigure(i % 2, weight=1)
                tk.Label(cell, text=lbl, bg=P["bg2"], fg=P["t3"],
                         font=self._mono_xs, padx=8, pady=4).pack(anchor="w")
                tk.Label(cell, text=val, bg=P["bg2"], fg=col,
                         font=self._mono_l, padx=8, pady=2).pack(anchor="w")

            # Tags
            tags = node.get("tags") or []
            if tags:
                self._insp_section(inner, "TAGS")
                tag_row = tk.Frame(inner, bg=P["bg1"])
                tag_row.pack(fill="x", padx=10, pady=4)
                for t in tags:
                    tk.Label(tag_row, text=t, bg=P["bg3"], fg=accent,
                             font=self._mono_xs, padx=5, pady=2,
                             highlightbackground=P["line2"],
                             highlightthickness=1).pack(side="left", padx=2, pady=2)

            # Definitions
            defs = node.get("definitions") or []
            if defs:
                self._insp_section(inner, f"DEFINITIONS ({len(defs)})")
                for d in defs:
                    icon = KIND_ICON.get(d.get("kind", ""), "·")
                    self._insp_row(inner, icon, d.get("name", ""), accent,
                                   f":{d['line']}" if d.get("line") else "")

            # Imports (out edges)
            if out_edges:
                self._insp_section(inner, f"IMPORTS ({len(out_edges)})")
                for e in out_edges:
                    tgt = node_map.get(e["target"])
                    _, _, ta, _ = cat_style(tgt.get("category", "other") if tgt else "other")
                    self._insp_row(inner, "→",
                                   tgt.get("label", e["target"]) if tgt else e["target"],
                                   ta,
                                   ", ".join(e.get("symbols") or [])[:20])

            # Imported by (in edges)
            if in_edges:
                self._insp_section(inner, f"IMPORTED BY ({len(in_edges)})")
                for e in in_edges:
                    src = node_map.get(e["source"])
                    _, _, sa, _ = cat_style(src.get("category", "other") if src else "other")
                    self._insp_row(inner, "←",
                                   src.get("label", e["source"]) if src else e["source"],
                                   sa,
                                   ", ".join(e.get("symbols") or [])[:20])

            # Doc warnings
            if warns:
                self._insp_section(inner, "⚠ DOC WARNINGS")
                for w in warns:
                    tk.Label(inner, text=w.get("message", ""), bg=P["bg1"],
                             fg=P["amber"], font=self._mono_xs,
                             padx=12, pady=3, anchor="w",
                             wraplength=240).pack(fill="x")

            # Errors
            errors = node.get("errors") or []
            if errors:
                self._insp_section(inner, "ERRORS")
                for err in errors:
                    tk.Label(inner, text=err, bg=P["bg1"],
                             fg=P["red"], font=self._mono_xs,
                             padx=12, pady=2, anchor="w",
                             wraplength=240).pack(fill="x")

            # Live timing — from .side-metrics.json if available
            file_m = self._node_metrics(node.get("path", ""))
            if file_m:
                self._insp_section(inner, "LIVE TIMING")
                age = time.time() - file_m.get("last_ts", 0.0)
                stale = age > 8
                col   = P["t3"] if stale else (
                    P["green"] if file_m.get("avg_ms", 0) < 10 else
                    P["amber"] if file_m.get("avg_ms", 0) < 100 else P["red"]
                )
                stats_frame = tk.Frame(inner, bg=P["bg2"],
                                        highlightbackground=P["line"], highlightthickness=1)
                stats_frame.pack(fill="x", padx=12, pady=4)
                stat_rows = [
                    ("calls",   str(file_m.get("calls", 0))),
                    ("avg",     f'{file_m.get("avg_ms", 0):.1f}ms'),
                    ("max",     f'{file_m.get("max_ms", 0):.1f}ms'),
                    ("last",    f'{file_m.get("last_ms", 0):.1f}ms'),
                    ("age",     f'{age:.0f}s ago' if not stale else "stale"),
                ]
                for lbl, val in stat_rows:
                    r = tk.Frame(stats_frame, bg=P["bg2"])
                    r.pack(fill="x", padx=8, pady=1)
                    tk.Label(r, text=lbl, bg=P["bg2"], fg=P["t3"],
                             font=self._mono_xs, width=8, anchor="w").pack(side="left")
                    tk.Label(r, text=val, bg=P["bg2"], fg=col,
                             font=self._mono_s, anchor="w").pack(side="left")

                # Per-function breakdown for this file
                node_path = node.get("path", "")
                fn_prefix = node_path.replace("\\", "/")
                
                mw = self._metrics_watcher
                if mw:
                    fn_data = mw.get_function_metrics()
                else:
                    fn_data = {}
                
                fn_metrics = {
                    k.split("::")[-1]: v
                    for k, v in fn_data.items()
                    if "::" in k and k.split("::")[0].replace("\\", "/").endswith(fn_prefix)
                }

                if fn_metrics:
                    self._insp_section(inner, f"FUNCTIONS ({len(fn_metrics)})")
                    for fn_name, fm in sorted(fn_metrics.items(),
                                               key=lambda x: -x[1].get("avg_ms", 0))[:8]:
                        fn_col = (P["green"] if fm.get("avg_ms", 0) < 10
                                  else P["amber"] if fm.get("avg_ms", 0) < 100
                                  else P["red"])
                        self._insp_row(
                            inner, "ƒ", fn_name, fn_col,
                            f'{fm.get("avg_ms", 0):.1f}ms × {fm.get("calls", 0)}'
                        )
            elif self._metrics_watcher:
                # Watcher is running but no data for this file yet
                mw_active = self._metrics_watcher.is_active()
                if not mw_active:
                    self._insp_section(inner, "LIVE TIMING")
                    tk.Label(inner,
                             text="No timing data. Add to your project:\n"
                                  "  from monitor.instrument import timed\n"
                                  "  @timed\n  def your_function(): ...",
                             bg=P["bg1"], fg=P["t3"], font=self._mono_xs,
                             padx=12, pady=4, anchor="w", justify="left").pack(fill="x")

        self._open_inspector(build)

    def _inspect_edge(self, edge: dict):
        color, _ = edge_style(edge.get("type", ""))
        node_map = {n["id"]: n for n in self._vis_nodes()}
        src = node_map.get(edge["source"])
        tgt = node_map.get(edge["target"])

        def build(parent):
            self._insp_header(parent, "Edge", edge.get("type", ""), color)
            inner = tk.Frame(parent, bg=P["bg1"])
            inner.pack(fill="both", expand=True, padx=10, pady=8)

            flow = tk.Frame(inner, bg=P["bg2"],
                             highlightbackground=P["line"], highlightthickness=1)
            flow.pack(fill="x", pady=4)
            src_lbl = src.get("label", edge["source"]) if src else edge["source"]
            tgt_lbl = tgt.get("label", edge["target"]) if tgt else edge["target"]
            tk.Label(flow, text=src_lbl, bg=P["bg2"], fg=P["t1"],
                     font=self._mono_s, pady=8, padx=8).pack(side="left")
            tk.Label(flow, text="──→", bg=P["bg2"], fg=color,
                     font=self._mono_s).pack(side="left", fill="x", expand=True)
            tk.Label(flow, text=tgt_lbl, bg=P["bg2"], fg=P["t1"],
                     font=self._mono_s, pady=8, padx=8).pack(side="right")

            tk.Label(inner, text=edge.get("type", ""), bg=P["bg3"], fg=color,
                     font=self._mono_xs, padx=6, pady=2,
                     highlightbackground=color, highlightthickness=1).pack(anchor="w", pady=4)

            if edge.get("symbols"):
                self._insp_section(inner, "SYMBOLS")
                for sym in edge["symbols"]:
                    tk.Label(inner, text=f"ƒ {sym}", bg=P["bg1"], fg=P["t1"],
                             font=self._mono_s, padx=12, pady=2, anchor="w").pack(fill="x")

            if edge.get("line"):
                tk.Label(inner, text=f"source line {edge['line']}", bg=P["bg1"],
                         fg=P["t3"], font=self._mono_xs, padx=0, pady=4).pack(anchor="w")

        self._open_inspector(build)

    def _inspect_doc_health(self):
        if not self.graph:
            return
        docs = self.graph.get("meta", {}).get("docs", {})
        summary = docs.get("summary", {})
        warnings = docs.get("warnings", [])

        def build(parent):
            self._insp_header(parent, "Doc Health", "documentation audit", P["amber"])
            inner = tk.Frame(parent, bg=P["bg1"])
            inner.pack(fill="both", expand=True, padx=10, pady=8)

            stats = [
                ("MISSING", summary.get("missingReadmes", 0), P["red"]),
                ("STALE",   summary.get("staleReadmes",   0), P["amber"]),
                ("EMPTY",   summary.get("emptyModules",   0), P["blue"]),
                ("TOTAL",   summary.get("total",          0), P["t2"]),
            ]
            grid = tk.Frame(inner, bg=P["bg1"])
            grid.pack(fill="x", pady=4)
            for i, (lbl, val, col) in enumerate(stats):
                cell = tk.Frame(grid, bg=P["bg2"],
                                highlightbackground=P["line"], highlightthickness=1)
                cell.grid(row=0, column=i, padx=3, pady=3, sticky="ew")
                grid.columnconfigure(i, weight=1)
                tk.Label(cell, text=lbl, bg=P["bg2"], fg=P["t3"],
                         font=self._mono_xs, padx=6, pady=4).pack()
                tk.Label(cell, text=str(val), bg=P["bg2"], fg=col,
                         font=self._mono_l, padx=6).pack()

            for w in warnings:
                wframe = tk.Frame(inner, bg=P["bg2"],
                                   highlightbackground=P["line"], highlightthickness=1)
                wframe.pack(fill="x", pady=3)
                col = P["red"] if w.get("type") == "missing-readme" else P["amber"]
                tk.Label(wframe, text=w.get("type", ""), bg=P["bg2"], fg=col,
                         font=self._mono_xs, padx=8, pady=4, anchor="w").pack(fill="x")
                tk.Label(wframe, text=w.get("message", ""), bg=P["bg2"], fg=P["t2"],
                         font=self._mono_xs, padx=8, pady=2, anchor="w",
                         wraplength=240).pack(fill="x")

        self._open_inspector(build)

    def _node_warnings(self, node_id: str) -> list:
        """O(1) lookup using pre-built index from _apply_graph."""
        return getattr(self, "_warnings_index", {}).get(node_id, [])

    # ── Status bar ────────────────────────────────────────────────────────────

    def _update_status_bar(self):
        if not self.graph:
            return
        meta = self.graph.get("meta", {})
        for w in self._sb_langs_frame.winfo_children():
            w.destroy()
        for lang, stats in meta.get("languages", {}).items():
            _, _, accent, _ = cat_style(lang)
            row = tk.Frame(self._sb_langs_frame, bg=P["bg0"])
            row.pack(side="left", padx=6)
            tk.Frame(row, bg=accent, width=5, height=5).pack(side="left", padx=(0, 3))
            tk.Label(row, text=f"{lang.upper()} {stats['files']}f",
                     bg=P["bg0"], fg=P["t2"], font=self._mono_xs).pack(side="left")

        parsed_at = meta.get("parsedAt", "")[:19].replace("T", " ")
        parse_ms  = meta.get("parseTime", 0)
        perf      = meta.get("perf", {})
        slowest   = perf.get("slowest", "")
        perf_hint = f"  slow: {slowest}" if slowest else ""
        self._sb_parsed_var.set(f"parsed {parsed_at} ({parse_ms}ms{perf_hint})")

        # Self-monitoring badge
        is_self = getattr(self, "_is_self", False)
        if hasattr(self, "_self_badge_var"):
            self._self_badge_var.set("◈ SELF" if is_self else "")

    def _update_doc_badge(self):
        if not self.graph:
            return
        n = self.graph.get("meta", {}).get("docs", {}).get("summary", {}).get("total", 0)
        if n > 0:
            self._doc_badge_var.set(f"⚠ {n} warn{'s' if n > 1 else ''}")
            self._doc_badge.grid()
        else:
            self._doc_badge.grid_remove()

    # ── Loading overlay ───────────────────────────────────────────────────────

    def _show_loading(self, msg: str = "parsing…"):
        # Cancel any previous animation loop before creating a new window
        self._stop_loading_animation()

        if self._loading_win and self._loading_win.winfo_exists():
            # Already open — just update the message
            if hasattr(self, "_loading_msg"):
                self._loading_msg.config(text=msg)
            self._loading_progress = 0
            self._start_loading_animation()
            return

        # Use a regular Toplevel (no overrideredirect — avoids sticky behaviour)
        self._loading_win = tk.Toplevel(self)
        self._loading_win.title("")
        self._loading_win.configure(bg=P["bg0"])
        self._loading_win.resizable(False, False)
        # Remove decorations portably: no min/max/close buttons where supported
        try:
            self._loading_win.attributes("-toolwindow", True)   # Windows
        except Exception:
            pass
        try:
            self._loading_win.attributes("-alpha", 0.94)
        except Exception:
            pass

        # Keep it above the main window but not system-wide topmost
        self._loading_win.transient(self)

        # Centre over main window
        self.update_idletasks()
        x = self.winfo_x() + self.winfo_width() // 2 - 160
        y = self.winfo_y() + self.winfo_height() // 2 - 50
        self._loading_win.geometry(f"320x110+{x}+{y}")

        tk.Label(self._loading_win, text="S-IDE", bg=P["bg0"], fg=P["t2"],
                 font=self._mono_l).pack(pady=(18, 4))
        self._loading_msg = tk.Label(self._loading_win, text=msg,
                                      bg=P["bg0"], fg=P["t3"],
                                      font=self._mono_xs)
        self._loading_msg.pack()
        bar_bg = tk.Frame(self._loading_win, bg=P["bg3"], height=2, width=200)
        bar_bg.pack(pady=10)
        self._loading_fill = tk.Frame(bar_bg, bg=P["green"], height=2, width=0)
        self._loading_fill.place(x=0, y=0)
        self._loading_progress = 0
        self._loading_after_id = None
        self._start_loading_animation()

    def _start_loading_animation(self):
        """Kick off the progress bar animation. Safe to call multiple times."""
        self._stop_loading_animation()
        self._loading_progress = getattr(self, "_loading_progress", 0)
        self._tick_loading()

    def _tick_loading(self):
        """Single animation tick — reschedules itself until window is gone."""
        if not self._loading_win or not self._loading_win.winfo_exists():
            self._loading_after_id = None
            return
        self._loading_progress = min(self._loading_progress + 2, 88)
        try:
            w = int(200 * self._loading_progress / 100)
            self._loading_fill.config(width=w)
        except Exception:
            pass
        self._loading_after_id = self.after(50, self._tick_loading)

    def _stop_loading_animation(self):
        """Cancel the animation loop so it never orphans."""
        aid = getattr(self, "_loading_after_id", None)
        if aid is not None:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
            self._loading_after_id = None

    def _hide_loading(self):
        self._stop_loading_animation()
        # Destroy immediately — don't defer with after(), which can
        # block behind a heavy redraw and leave the popup frozen.
        self._destroy_loading_win()

    def _destroy_loading_win(self):
        self._stop_loading_animation()
        if hasattr(self, "_loading_win"):
            try:
                self._loading_win.destroy()
            except Exception:
                pass
            del self._loading_win

    # ── Version management ────────────────────────────────────────────────────

    def _archive_version(self):
        if not self.graph:
            messagebox.showinfo("S-IDE", "Load a project first.")
            return
        root = self.graph["meta"]["root"]
        try:
            path = archive_version(root)
            self._refresh_version_list()
            messagebox.showinfo("Archived", f"Snapshot saved:\n{os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Archive Error", str(exc))

    def _compress_versions(self):
        if not self.graph:
            return
        root = self.graph["meta"]["root"]
        try:
            results = compress_versions(root)
            self._refresh_version_list()
            ok = sum(1 for r in results if "tarball" in r)
            messagebox.showinfo("Compressed", f"Compressed {ok} director{'y' if ok==1 else 'ies'}.")
        except Exception as exc:
            messagebox.showerror("Compress Error", str(exc))

    def _apply_update_dialog(self):
        if not self.graph:
            messagebox.showinfo("S-IDE", "Load a project first.")
            return
        path = filedialog.askopenfilename(
            title="Select Update Tarball",
            filetypes=[("Tarballs", "*.tar.gz *.tgz"), ("All files", "*")],
        )
        if not path:
            return
        root = self.graph["meta"]["root"]
        bump = self._bump_var.get()
        try:
            new_ver, arch = apply_update(root, path, bump)
            self._refresh_version_list()
            self._load_project(root)   # re-parse after update
        except Exception as exc:
            messagebox.showerror("Update Error", str(exc))

    def _run_self_update(self):
        """Run update.py in a background process, streaming output to proc panel."""
        import sys
        update_script = os.path.join(_ROOT_DIR, "update.py")
        if not os.path.isfile(update_script):
            messagebox.showerror("Update", f"update.py not found at:\n{update_script}")
            return
        cmd = f"{sys.executable} {update_script} --yes --no-relaunch"
        self._log.info("Running self-update: %s", cmd)
        if self._proc_mgr is None:
            self._proc_mgr = ProcessManager()
        proc = self._proc_mgr.start(name="self-update", command=cmd, cwd=_ROOT_DIR)
        self.processes[proc.id] = proc

        def _on_line(line, pid=proc.id):
            self.after(0, lambda: self._append_proc_log(pid, line, False))
            self._log.info("[self-update] %s", line)
        def _on_err(line, pid=proc.id):
            self.after(0, lambda: self._append_proc_log(pid, line, True))
            self._log.warning("[self-update] %s", line)
        def _on_exit(code, pid=proc.id):
            self._log.info("self-update exited with code %s", code)
            if code == 0:
                self.after(0, lambda: messagebox.showinfo(
                    "Update complete",
                    "Update applied.\nRestart S-IDE to load the new version.\n\n"
                    f"Run:\n  python {os.path.join(_ROOT_DIR, 'gui', 'app.py')}"
                ))
            else:
                self.after(0, lambda: messagebox.showerror(
                    "Update failed", f"update.py exited with code {code}.\nCheck the PROC panel for details."
                ))
        proc.on_stdout(_on_line)
        proc.on_stderr(_on_err)
        proc.on_exit(_on_exit)

        # Open proc panel to show progress
        if not (self._proc_win and self._proc_win.winfo_exists()):
            self._build_proc_panel()
        self.after(100, self._render_proc_list)


    def _refresh_version_list(self):
        for w in self._ver_list_frame.winfo_children():
            w.destroy()
        if not self.graph:
            return
        root = self.graph["meta"]["root"]
        try:
            versions = list_versions(root)
        except Exception:
            versions = []

        if not versions:
            tk.Label(self._ver_list_frame, text="no archives yet",
                     bg=P["bg1"], fg=P["t3"], font=self._mono_xs,
                     padx=2, pady=4).pack(anchor="w")
            return

        for v in versions[:10]:
            row = tk.Frame(self._ver_list_frame, bg=P["bg1"])
            row.pack(fill="x", pady=1)
            icon = "🗜" if v["type"] == "tarball" else "📁"
            tk.Label(row, text=icon, bg=P["bg1"], fg=P["t3"],
                     font=self._mono_xs).pack(side="left")
            tk.Label(row, text=v["name"][:26], bg=P["bg1"], fg=P["t2"],
                     font=self._mono_xs).pack(side="left", fill="x", expand=True)
            lb = tk.Label(row, text=fmt_size(v["size"]), bg=P["bg1"], fg=P["t3"],
                     font=self._mono_xs)
            lb.pack(side="right", padx=4)
            if self._ver_list_frame:
                tk.Frame(self._ver_list_frame, bg=P["line"], height=1).pack(fill="x")

    # ── Process panel ─────────────────────────────────────────────────────────

    def _toggle_proc_panel(self):
        pw = self._proc_win
        if pw is not None and pw.winfo_exists():
            self._proc_win.destroy()
            return
        self._build_proc_panel()

    def _build_proc_panel(self):
        self._proc_win = tk.Toplevel(self)
        self._proc_win.title("S-IDE — Processes")
        self._proc_win.configure(bg=P["bg1"])
        self._proc_win.geometry("480x560")
        self._proc_win.resizable(True, True)
        self._proc_win.transient(self)

        # Header
        hdr = tk.Frame(self._proc_win, bg=P["bg2"])
        hdr.pack(fill="x")
        tk.Frame(hdr, bg=P["line"], height=1).pack(fill="x")
        inner_hdr = tk.Frame(hdr, bg=P["bg2"])
        inner_hdr.pack(fill="x", padx=14, pady=8)
        tk.Label(inner_hdr, text="⚡ PROCESSES", bg=P["bg2"], fg=P["t1"],
                 font=self._mono_l).pack(side="left")
        self._proc_count_var = tk.StringVar(value="0")
        tk.Label(inner_hdr, textvariable=self._proc_count_var,
                 bg=P["bg3"], fg=P["t2"], font=self._mono_xs,
                 padx=6, pady=2).pack(side="left", padx=8)

        clear_stopped_btn = tk.Label(inner_hdr, text="✕ clear stopped",
                                      bg=P["bg3"], fg=P["t2"],
                                      font=self._mono_xs, padx=6, pady=2,
                                      cursor="hand2",
                                      highlightbackground=P["line2"], highlightthickness=1)
        clear_stopped_btn.pack(side="right", padx=4)
        clear_stopped_btn.bind("<Button-1>", lambda _: self._clear_stopped_procs())

        # Process list (scrollable)
        list_outer = tk.Frame(self._proc_win, bg=P["bg1"])
        list_outer.pack(fill="both", expand=True)
        scroll = tk.Scrollbar(list_outer)
        scroll.pack(side="right", fill="y")
        self._proc_list_canvas = tk.Canvas(list_outer, bg=P["bg1"],
                                            yscrollcommand=scroll.set,
                                            highlightthickness=0)
        if self._proc_list_canvas:
            self._proc_list_canvas.pack(fill="both", expand=True)
            scroll.config(command=self._proc_list_canvas.yview)
        
            self._proc_list_inner = tk.Frame(self._proc_list_canvas, bg=P["bg1"])
            self._proc_list_canvas.create_window((0, 0), window=self._proc_list_inner,
                                                 anchor="nw")
            # Keep inner frame width in sync with canvas width
            def _on_canvas_resize(e):
                if self._proc_list_canvas:
                    self._proc_list_canvas.itemconfig(1, width=e.width)
            self._proc_list_canvas.bind("<Configure>", _on_canvas_resize)
            if self._proc_list_inner:
                self._proc_list_inner.bind("<Configure>",
                    lambda e: self._proc_list_canvas.configure(
                        scrollregion=self._proc_list_canvas.bbox("all")) if self._proc_list_canvas else None)

        # Command input
        tk.Frame(self._proc_win, bg=P["line"], height=1).pack(fill="x")
        cmd_row = tk.Frame(self._proc_win, bg=P["bg2"])
        cmd_row.pack(fill="x", padx=10, pady=8)

        self._proc_cmd_var = tk.StringVar()
        cmd_entry = tk.Entry(cmd_row, textvariable=self._proc_cmd_var,
                              bg=P["bg3"], fg=P["t1"],
                              insertbackground=P["green"],
                              bd=0, font=self._mono_s, width=24)
        cmd_entry.pack(side="left", fill="x", expand=True,
                       ipady=4, padx=(0, 6))
        cmd_entry.insert(0, "command…")
        cmd_entry.bind("<FocusIn>",
                       lambda e: (cmd_entry.delete(0, "end")
                                  if cmd_entry.get() == "command…" else None))
        cmd_entry.bind("<Return>", lambda _: self._start_process())

        self._proc_cwd_var = tk.StringVar(
            value=self.graph["meta"]["root"] if self.graph else "")
        cwd_entry = tk.Entry(cmd_row, textvariable=self._proc_cwd_var,
                              bg=P["bg3"], fg=P["t2"],
                              insertbackground=P["green"],
                              bd=0, font=self._mono_xs, width=14)
        cwd_entry.pack(side="left", ipady=4, padx=(0, 6))

        run_btn = tk.Label(cmd_row, text="▶ Run", bg=P["green2"], fg=P["green"],
                           font=self._mono_s, padx=8, pady=4,
                           cursor="hand2",
                           highlightbackground=P["green"], highlightthickness=1)
        run_btn.pack(side="left")
        run_btn.bind("<Button-1>", lambda _: self._start_process())

        self._render_proc_list()

    def _clear_stopped_procs(self):
        """Remove stopped/crashed processes from the registry and redraw."""
        to_remove = [
            pid for pid, p in self.processes.items()
            if p.info()["status"] in ("stopped", "crashed")
        ]
        for pid in to_remove:
            self.processes.pop(pid, None)
        if self._proc_mgr:
            self._proc_mgr.purge_stopped()
        self._render_proc_list()

    def _start_process(self):
        cmd = self._proc_cmd_var.get().strip()
        if not cmd or cmd == "command…":
            return
        cwd = self._proc_cwd_var.get().strip() or (
            self.graph["meta"]["root"] if self.graph else os.getcwd())

        if self._proc_mgr is None:
            self._proc_mgr = ProcessManager()

        name = cmd.split()[0]
        proc = self._proc_mgr.start(name=name, command=cmd, cwd=cwd)
        self.processes[proc.id] = proc
        self._proc_cmd_var.set("")

        # Start / restart process monitor
        self._ensure_proc_monitor()
        self._render_proc_list()

        # Subscribe to output
        def _on_line(line, proc_id=proc.id):
            self.after(0, lambda: self._append_proc_log(proc_id, line, False))
        def _on_err(line, proc_id=proc.id):
            self.after(0, lambda: self._append_proc_log(proc_id, line, True))
        def _on_exit(code, proc_id=proc.id):
            self.after(0, lambda: self._render_proc_list())

        proc.on_stdout(_on_line)
        proc.on_stderr(_on_err)
        proc.on_exit(_on_exit)

    def _render_proc_list(self):
        if not hasattr(self, "_proc_list_inner"):
            return
        for w in self._proc_list_inner.winfo_children():
            w.destroy()

        STATUS_COLOURS = {
            "running":   P["green"],
            "stopped":   P["t3"],
            "suspended": P["amber"],
            "crashed":   P["red"],
        }

        all_procs = list(self.processes.values())
        if hasattr(self, "_proc_count_var"):
            self._proc_count_var.set(str(len(all_procs)))

        for proc in reversed(all_procs):
            info = proc.info()
            row = tk.Frame(self._proc_list_inner, bg=P["bg2"])
            row.pack(fill="x", pady=1)
            tk.Frame(row, bg=P["line"], height=1).pack(fill="x")
            inner = tk.Frame(row, bg=P["bg2"])
            inner.pack(fill="x", padx=12, pady=6)

            status_col = STATUS_COLOURS.get(info["status"], P["t3"])
            # Pulsing dot for running processes
            dot = tk.Frame(inner, bg=status_col, width=7, height=7)
            dot.pack(side="left", padx=(0, 8))
            tk.Label(inner, text=info["name"][:22], bg=P["bg2"], fg=P["t1"],
                     font=self._mono_s).pack(side="left", fill="x", expand=True)

            # CPU / RSS from ProcessMonitor if available
            if hasattr(self, "_proc_monitor") and self._proc_monitor:
                sample = self._proc_monitor.latest(info["id"])
                if sample:
                    cpu_txt = f"cpu:{sample['cpu']:.0f}%"
                    rss_txt = f"rss:{sample['rss']:.0f}MB"
                    tk.Label(inner, text=cpu_txt, bg=P["bg2"], fg=P["t2"],
                             font=self._mono_xs).pack(side="left", padx=(0, 4))
                    tk.Label(inner, text=rss_txt, bg=P["bg2"], fg=P["t2"],
                             font=self._mono_xs).pack(side="left", padx=(0, 4))
            if info.get("pid"):
                tk.Label(inner, text=f"pid:{info['pid']}", bg=P["bg2"],
                         fg=P["t3"], font=self._mono_xs).pack(side="left", padx=4)

            btn_row = tk.Frame(inner, bg=P["bg2"])
            btn_row.pack(side="right")

            def _mk_btn(text, cmd, danger=False):
                b = tk.Label(btn_row, text=text, bg=P["bg3"], fg=P["t2"],
                             font=self._mono_xs, padx=6, pady=2,
                             cursor="hand2",
                             highlightbackground=P["line2"], highlightthickness=1)
                b.pack(side="left", padx=2)
                b.bind("<Button-1>", lambda _, c=cmd: c())
                if danger:
                    b.bind("<Enter>", lambda _, w=b: w.config(
                        fg=P["red"], highlightbackground=P["red"]))
                    b.bind("<Leave>", lambda _, w=b: w.config(
                        fg=P["t2"], highlightbackground=P["line2"]))
                return b

            if info["status"] == "running":
                _mk_btn("⏸", lambda p=proc: (p.suspend(), self._render_proc_list()))
                _mk_btn("✕", lambda p=proc: (p.stop(), self._render_proc_list()), danger=True)
            elif info["status"] == "suspended":
                _mk_btn("▶", lambda p=proc: (p.resume(), self._render_proc_list()))
                _mk_btn("✕", lambda p=proc: (p.stop(), self._render_proc_list()), danger=True)

            # Log output
            log_frame = tk.Frame(self._proc_list_inner, bg=P["bg0"])
            log_frame.pack(fill="x")
            log_text = tk.Text(log_frame, bg=P["bg0"], fg=P["t2"],
                               font=self._mono_xs, height=3,
                               bd=0, state="disabled",
                               insertbackground=P["green"],
                               wrap="char")
            log_text.pack(fill="x", padx=12, pady=(0, 4))
            log_text.tag_config("err", foreground=P["red"])

            # Fill existing log lines
            for entry in proc.logs()[-30:]:
                log_text.config(state="normal")
                tag = "err" if entry["stream"] == "stderr" else ""
                log_text.insert("end", entry["line"] + "\n", tag)
                log_text.config(state="disabled")
            log_text.see("end")
            self._proc_log_widgets = getattr(self, "_proc_log_widgets", {})
            self._proc_log_widgets[proc.id] = log_text

    def _ensure_proc_monitor(self):
        """Start ProcessMonitor if we have running processes and it's not running."""
        if not self._proc_mgr:
            return
        if not hasattr(self, "_proc_monitor") or self._proc_monitor is None:
            self._proc_monitor = ProcessMonitor(self._proc_mgr)
            self._proc_monitor.start()
            self._log.debug("ProcessMonitor started")
        # Schedule periodic UI refresh while any process is running
        self._schedule_proc_refresh()

    def _schedule_proc_refresh(self):
        """Refresh proc panel metrics every 2.5s while any process is running."""
        running = any(
            p.info()["status"] == "running"
            for p in self.processes.values()
        )
        if running and self._proc_list_inner and self._proc_list_inner.winfo_exists():
            self._render_proc_list()
            self.after(2500, self._schedule_proc_refresh)

    def _append_proc_log(self, proc_id: str, line: str, is_err: bool):
        widgets = getattr(self, "_proc_log_widgets", {})
        w = widgets.get(proc_id)
        if w and w.winfo_exists():
            w.config(state="normal")
            tag = "err" if is_err else ""
            w.insert("end", line + "\n", tag)
            w.see("end")
            w.config(state="disabled")


    def _toggle_log_panel(self):
        """Open or close the floating log panel."""
        if self._log_win and self._log_win.winfo_exists():
            self._log_win.destroy()
            return
        self._build_log_panel()

    def _build_log_panel(self):
        self._log_win = tk.Toplevel(self)
        self._log_win.title("S-IDE — Log")
        self._log_win.configure(bg=P["bg0"])
        self._log_win.geometry("680x400")
        self._log_win.resizable(True, True)
        self._log_win.transient(self)

        # Header
        hdr = tk.Frame(self._log_win, bg=P["bg1"])
        hdr.pack(fill="x")
        tk.Frame(hdr, bg=P["line"], height=1).pack(fill="x")
        hdr_inner = tk.Frame(hdr, bg=P["bg1"])
        hdr_inner.pack(fill="x", padx=12, pady=6)
        tk.Label(hdr_inner, text="DEBUG LOG", bg=P["bg1"], fg=P["t1"],
                 font=self._mono_l).pack(side="left")

        log_path = get_log_path()
        tk.Label(hdr_inner, text=f"→ {log_path}",
                 bg=P["bg1"], fg=P["t3"],
                 font=self._mono_xs).pack(side="left", padx=10)

        clear_btn = tk.Label(hdr_inner, text="CLEAR", bg=P["bg3"], fg=P["t2"],
                              font=self._mono_xs, padx=6, pady=2, cursor="hand2",
                              highlightbackground=P["line2"], highlightthickness=1)
        clear_btn.pack(side="right", padx=4)

        refresh_btn = tk.Label(hdr_inner, text="↻ REFRESH", bg=P["bg3"], fg=P["t2"],
                                font=self._mono_xs, padx=6, pady=2, cursor="hand2",
                                highlightbackground=P["line2"], highlightthickness=1)
        refresh_btn.pack(side="right", padx=4)

        tk.Frame(hdr, bg=P["line"], height=1).pack(fill="x")

        # Log text area
        txt_frame = tk.Frame(self._log_win, bg=P["bg0"])
        txt_frame.pack(fill="both", expand=True)
        scroll = tk.Scrollbar(txt_frame)
        scroll.pack(side="right", fill="y")
        self._log_text = tk.Text(
            txt_frame,
            bg=P["bg0"], fg=P["t1"],
            font=(self._mono_xs.actual()["family"], 9),
            yscrollcommand=scroll.set,
            bd=0, wrap="char", state="disabled",
            selectbackground=P["bg3"],
        )
        self._log_text.pack(fill="both", expand=True, padx=6, pady=4)
        scroll.config(command=self._log_text.yview)

        # Colour tags
        self._log_text.tag_config("DEBUG",   foreground=P["t2"])
        self._log_text.tag_config("INFO",    foreground=P["t1"])
        self._log_text.tag_config("WARNING", foreground=P["amber"])
        self._log_text.tag_config("ERROR",   foreground=P["red"])
        self._log_text.tag_config("CRITICAL",foreground=P["red"])

        def _refresh():
            self._log_text.config(state="normal")
            self._log_text.delete("1.0", "end")
            for level, msg in recent_lines():
                tag = level if level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL") else "INFO"
                self._log_text.insert("end", msg + "\n", tag)
            self._log_text.config(state="disabled")
            self._log_text.see("end")

        def _clear():
            clear_ring()
            self._log_text.config(state="normal")
            self._log_text.delete("1.0", "end")
            self._log_text.config(state="disabled")

        clear_btn.bind("<Button-1>", lambda _: _clear())
        # Auto-refresh every 2s while panel is open
        def _auto_refresh():
            if self._log_win and self._log_win.winfo_exists():
                _refresh()
                self._log_win.after(2000, _auto_refresh)
        _auto_refresh()

        # Also print the log file path to stdout for terminal users
        import sys
        print(f"[s-ide] Log file: {get_log_path()}", file=sys.stderr)


    # ── Build panel ───────────────────────────────────────────────────────────

    def _toggle_build_panel(self):
        """Open or close the floating build panel."""
        if self._build_win and self._build_win.winfo_exists():
            self._build_win.destroy()
            return
        self._open_build_panel()

    def _open_build_panel(self):
        self._build_win = tk.Toplevel(self)
        self._build_win.title("S-IDE — Build")
        self._build_win.configure(bg=P["bg1"])
        self._build_win.geometry("560x580")
        self._build_win.resizable(True, True)
        self._build_win.transient(self)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(self._build_win, bg=P["bg2"])
        hdr.pack(fill="x")
        tk.Frame(hdr, bg=P["line"], height=1).pack(fill="x")
        hdr_inner = tk.Frame(hdr, bg=P["bg2"])
        hdr_inner.pack(fill="x", padx=14, pady=8)
        tk.Label(hdr_inner, text="🔨 BUILD", bg=P["bg2"], fg=P["t1"],
                 font=self._mono_l).pack(side="left")
        tk.Frame(hdr, bg=P["line"], height=1).pack(fill="x")

        # ── Options ───────────────────────────────────────────────────────────
        opts_frame = tk.Frame(self._build_win, bg=P["bg1"])
        opts_frame.pack(fill="x", padx=14, pady=8)

        def _row(label, widget_fn):
            r = tk.Frame(opts_frame, bg=P["bg1"])
            r.pack(fill="x", pady=3)
            tk.Label(r, text=label, bg=P["bg1"], fg=P["t2"],
                     font=self._mono_xs, width=14, anchor="w").pack(side="left")
            return widget_fn(r)

        self._build_kind_var   = tk.StringVar(value="tarball")
        self._build_plat_var   = tk.StringVar(value="auto")
        self._build_bump_var   = tk.StringVar(value="none")
        self._build_minify_var = tk.BooleanVar(value=True)
        self._build_clean_var  = tk.BooleanVar(value=True)
        self._build_tests_var  = tk.BooleanVar(value=False)

        def _combo(parent, var, values, width=12):
            c = ttk.Combobox(parent, textvariable=var, values=values,
                              width=width, font=self._mono_xs, state="readonly")
            c.pack(side="left")
            return c

        def _check(parent, var, text):
            cb = tk.Checkbutton(parent, variable=var, text=text,
                                bg=P["bg1"], fg=P["t1"], selectcolor=P["bg3"],
                                activebackground=P["bg1"], font=self._mono_xs)
            cb.pack(side="left")
            return cb

        _row("Kind",     lambda p: _combo(p, self._build_kind_var,
                                          ["tarball", "installer", "portable"]))
        _row("Platform", lambda p: _combo(p, self._build_plat_var,
                                          ["auto", "linux", "macos", "windows"]))
        _row("Bump ver", lambda p: _combo(p, self._build_bump_var,
                                          ["none", "patch", "minor", "major"], width=8))

        flags_row = tk.Frame(opts_frame, bg=P["bg1"])
        flags_row.pack(fill="x", pady=3)
        tk.Label(flags_row, text="Options", bg=P["bg1"], fg=P["t2"],
                 font=self._mono_xs, width=14, anchor="w").pack(side="left")
        _check(flags_row, self._build_minify_var, "minify")
        _check(flags_row, self._build_clean_var,  "clean")
        _check(flags_row, self._build_tests_var,  "keep tests")

        tk.Frame(self._build_win, bg=P["line"], height=1).pack(fill="x")

        # ── Action buttons ────────────────────────────────────────────────────
        btn_row = tk.Frame(self._build_win, bg=P["bg1"])
        btn_row.pack(fill="x", padx=14, pady=8)

        def _action_btn(text, cmd, accent=False):
            col = P["green"] if accent else P["t2"]
            bdr = P["green2"] if accent else P["line2"]
            b = tk.Label(btn_row, text=text, bg=P["bg3"], fg=col,
                         font=self._mono_s, padx=10, pady=4, cursor="hand2",
                         highlightbackground=bdr, highlightthickness=1)
            b.pack(side="left", padx=(0, 6))
            b.bind("<Button-1>", lambda _: cmd())
            return b

        _action_btn("▶ Build", self._run_build, accent=True)
        _action_btn("🧹 Clean only", self._run_clean_only)
        _action_btn("📦 Archive", lambda: self._archive_version()
                     if self.graph else None)

        tk.Frame(self._build_win, bg=P["line"], height=1).pack(fill="x")

        # ── Perf breakdown ────────────────────────────────────────────────────
        tk.Label(self._build_win, text="LAST PARSE PERFORMANCE",
                 bg=P["bg1"], fg=P["t3"], font=self._mono_xs,
                 padx=14, pady=6, anchor="w").pack(fill="x")

        self._perf_frame = tk.Frame(self._build_win, bg=P["bg1"])
        self._perf_frame.pack(fill="x", padx=14, pady=(0, 6))
        self._refresh_perf_display()

        tk.Frame(self._build_win, bg=P["line"], height=1).pack(fill="x")

        # ── Live render timing ────────────────────────────────────────────────
        render_hdr = tk.Frame(self._build_win, bg=P["bg1"])
        render_hdr.pack(fill="x")
        tk.Label(render_hdr, text="LIVE RENDER TIMING",
                 bg=P["bg1"], fg=P["t3"], font=self._mono_xs,
                 padx=14, pady=6, anchor="w").pack(side="left", fill="x", expand=True)
        live_btn = tk.Label(render_hdr, text="▶ START", bg=P["bg3"], fg=P["green"],
                             font=self._mono_xs, padx=6, pady=2, cursor="hand2",
                             highlightbackground=P["green2"], highlightthickness=1)
        live_btn.pack(side="right", padx=14)

        self._render_timing_frame = tk.Frame(self._build_win, bg=P["bg1"])
        self._render_timing_frame.pack(fill="x", padx=14, pady=(0, 6))
        self._render_timing_active = False
        self._render_timing_after_id = None

        def _toggle_live_timing():
            self._render_timing_active = not self._render_timing_active
            if self._render_timing_active:
                live_btn.config(text="■ STOP", fg=P["red"],
                                highlightbackground=P["red"])
                self._update_render_timing()
            else:
                live_btn.config(text="▶ START", fg=P["green"],
                                highlightbackground=P["green2"])
                if self._render_timing_after_id:
                    self.after_cancel(self._render_timing_after_id)

        live_btn.bind("<Button-1>", lambda _: _toggle_live_timing())

        tk.Frame(self._build_win, bg=P["line"], height=1).pack(fill="x")

        # ── Build log / history ───────────────────────────────────────────────
        tk.Label(self._build_win, text="BUILD OUTPUT",
                 bg=P["bg1"], fg=P["t3"], font=self._mono_xs,
                 padx=14, pady=6, anchor="w").pack(fill="x")

        log_frame = tk.Frame(self._build_win, bg=P["bg0"])
        log_frame.pack(fill="both", expand=True)
        sb = tk.Scrollbar(log_frame)
        sb.pack(side="right", fill="y")
        self._build_log_text = tk.Text(
            log_frame, bg=P["bg0"], fg=P["t1"],
            font=(self._mono_xs.actual()["family"], 9),
            yscrollcommand=sb.set, bd=0, wrap="char", state="disabled",
        )
        self._build_log_text.pack(fill="both", expand=True, padx=6, pady=4)
        sb.config(command=self._build_log_text.yview)
        self._build_log_text.tag_config("ok",   foreground=P["green"])
        self._build_log_text.tag_config("err",  foreground=P["red"])
        self._build_log_text.tag_config("warn", foreground=P["amber"])
        self._build_log_text.tag_config("dim",  foreground=P["t3"])

        # Show existing build history
        self._show_build_history()

    def _build_log(self, msg: str, level: str = "info") -> None:
        """Append a line to the build output panel."""
        w = getattr(self, "_build_log_text", None)
        if not w or not w.winfo_exists():
            return
        tag = {"ok": "ok", "error": "err", "warn": "warn",
               "dim": "dim"}.get(level, "")
        w.config(state="normal")
        w.insert("end", msg + "\n", tag)
        w.see("end")
        w.config(state="disabled")

    def _refresh_perf_display(self) -> None:
        """Rebuild the parse-stage timing bar chart in the build panel."""
        for w in self._perf_frame.winfo_children():
            w.destroy()

        perf = (self.graph or {}).get("meta", {}).get("perf", {})
        stages = perf.get("stages", [])
        if not stages:
            tk.Label(self._perf_frame, text="no perf data yet — parse a project first",
                     bg=P["bg1"], fg=P["t3"], font=self._mono_xs).pack(anchor="w")
            return

        total = perf.get("total_ms", 1) or 1
        slowest = perf.get("slowest", "")

        for stage in stages:
            name = stage["name"]
            ms   = stage["ms"]
            pct  = ms / total

            row = tk.Frame(self._perf_frame, bg=P["bg1"])
            row.pack(fill="x", pady=1)

            is_slow = (name == slowest)
            accent  = P["amber"] if is_slow else P["green"]

            # Stage name
            tk.Label(row, text=name, bg=P["bg1"], fg=P["t1"],
                     font=self._mono_xs, width=16, anchor="w").pack(side="left")

            # Bar (fixed 160px wide container)
            bar_bg = tk.Frame(row, bg=P["bg3"], width=160, height=10)
            bar_bg.pack(side="left", padx=(4, 8))
            bar_bg.pack_propagate(False)
            bar_fill_w = max(2, int(160 * pct))
            tk.Frame(bar_bg, bg=accent, width=bar_fill_w, height=10).place(x=0, y=0)

            # Time label
            tk.Label(row, text=f"{ms:.1f}ms", bg=P["bg1"],
                     fg=P["amber"] if is_slow else P["t2"],
                     font=self._mono_xs, width=8, anchor="e").pack(side="left")

            # Percent
            tk.Label(row, text=f"{pct*100:.0f}%", bg=P["bg1"], fg=P["t3"],
                     font=self._mono_xs, width=4, anchor="e").pack(side="left")

        total_row = tk.Frame(self._perf_frame, bg=P["bg1"])
        total_row.pack(fill="x", pady=(4, 0))
        tk.Label(total_row, text="total", bg=P["bg1"], fg=P["t3"],
                 font=self._mono_xs, width=16, anchor="w").pack(side="left")
        tk.Label(total_row, text=f"{total:.1f}ms", bg=P["bg1"], fg=P["t1"],
                 font=self._mono_xs).pack(side="left", padx=(172, 0))

    def _update_render_timing(self) -> None:
        """Refresh the live render timing display in the build panel."""
        if not self._render_timing_active:
            return
        if not self._render_timing_frame or                 not self._render_timing_frame.winfo_exists():
            return

        for w in self._render_timing_frame.winfo_children():
            w.destroy()

        samples = self._render_times[-30:]   # last 30 frames

        if not samples:
            tk.Label(self._render_timing_frame,
                     text="no render data — interact with the canvas",
                     bg=P["bg1"], fg=P["t3"], font=self._mono_xs).pack(anchor="w")
        else:
            latest = samples[-1]
            avg_total = sum(s["total"] for s in samples) / len(samples)
            max_total = max(s["total"] for s in samples)
            fps_est   = round(1000 / avg_total) if avg_total > 0 else 0

            # Summary row
            summ = tk.Frame(self._render_timing_frame, bg=P["bg1"])
            summ.pack(fill="x", pady=(0, 4))
            colour = P["green"] if avg_total < 16 else P["amber"] if avg_total < 50 else P["red"]
            tk.Label(summ, text=f"avg {avg_total:.1f}ms",
                     bg=P["bg1"], fg=colour, font=self._mono_s).pack(side="left")
            tk.Label(summ, text=f"  max {max_total:.1f}ms",
                     bg=P["bg1"], fg=P["t2"], font=self._mono_xs).pack(side="left", padx=(8,0))
            tk.Label(summ, text=f"  ~{fps_est} fps",
                     bg=P["bg1"], fg=P["t2"], font=self._mono_xs).pack(side="left", padx=(8,0))
            tk.Label(summ,
                     text=f"  {latest['n_nodes']}n {latest['n_edges']}e",
                     bg=P["bg1"], fg=P["t3"], font=self._mono_xs).pack(side="right")

            # Per-phase breakdown using latest frame
            phases = [
                ("grid",    latest["grid"]),
                ("edges",   latest["edges"]),
                ("nodes",   latest["nodes"]),
                ("minimap", latest["minimap"]),
            ]
            max_phase = max(ms for _, ms in phases) or 1
            for phase_name, ms in phases:
                row = tk.Frame(self._render_timing_frame, bg=P["bg1"])
                row.pack(fill="x", pady=1)
                accent = P["red"] if ms > 16 else P["amber"] if ms > 8 else P["green"]
                tk.Label(row, text=phase_name, bg=P["bg1"], fg=P["t1"],
                         font=self._mono_xs, width=10, anchor="w").pack(side="left")
                bar_bg = tk.Frame(row, bg=P["bg3"], width=120, height=8)
                bar_bg.pack(side="left", padx=(4, 8))
                bar_bg.pack_propagate(False)
                bar_w = max(2, int(120 * ms / max(latest["total"], 1)))
                tk.Frame(bar_bg, bg=accent, width=bar_w, height=8).place(x=0, y=0)
                tk.Label(row, text=f"{ms:.1f}ms", bg=P["bg1"], fg=accent,
                         font=self._mono_xs, width=7, anchor="e").pack(side="left")

            # Sparkline — total ms over last 30 frames
            spark_h = 28
            spark_w = 240
            spark = tk.Canvas(self._render_timing_frame, bg=P["bg0"],
                              width=spark_w, height=spark_h,
                              highlightthickness=0)
            spark.pack(pady=(6, 0), anchor="w")
            if len(samples) > 1:
                vals = [s["total"] for s in samples]
                vmax = max(vals) or 1
                step = spark_w / (len(vals) - 1)
                pts = []
                for i, v in enumerate(vals):
                    x = i * step
                    y = spark_h - (v / vmax) * (spark_h - 2) - 1
                    pts.extend([x, y])
                if len(pts) >= 4:
                    spark.create_line(*pts, fill=colour, width=1.5, smooth=True)
                # 16ms target line
                y16 = spark_h - (16 / vmax) * (spark_h - 2) - 1
                if 0 <= y16 <= spark_h:
                    spark.create_line(0, y16, spark_w, y16,
                                      fill=P["t3"], dash=(3, 4), width=1)
                    spark.create_text(spark_w - 2, y16 - 6,
                                      text="16ms", fill=P["t3"],
                                      font=(self._mono_xs.actual()["family"], 7),
                                      anchor="e")

        self._render_timing_after_id = self.after(500, self._update_render_timing)

    def _run_build(self) -> None:
        """Run the full build pipeline in a background thread."""
        if not self.graph:
            self._build_log("No project loaded.", "warn")
            return
        root = self.graph["meta"]["root"]
        kind   = self._build_kind_var.get()
        plat   = self._build_plat_var.get()
        bump   = self._build_bump_var.get()
        minify = self._build_minify_var.get()
        clean  = self._build_clean_var.get()
        tests  = self._build_tests_var.get()

        self._build_log(f"── Build starting ──  kind={kind}  platform={plat}", "dim")
        self._build_log(f"   minify={minify}  clean={clean}  bump={bump}", "dim")

        def _do_build():
            try:
                from build.packager import package_project, PackageOptions
                opts = PackageOptions(
                    kind=kind,
                    target_platform=plat,
                    minify=minify,
                    clean=clean,
                    clean_tiers=["cache", "logs"],
                    strip_tests=not tests,
                )
                result = package_project(root, os.path.join(root, "dist"), opts)
                for err in result.errors:
                    self.after(0, lambda e=err: self._build_log(f"  WARN: {e}", "warn"))
                summary = result.summary()
                self.after(0, lambda s=summary: self._build_log(s, "ok"))
                self._log.info("Build complete: %s", summary)

                if bump and bump != "none":
                    from parser.project_config import (load_project_config,
                                                        save_project_config, bump_version)
                    cfg = load_project_config(root)
                    new_ver = bump_version(cfg.get("version", "0.0.0"), bump)
                    cfg["version"] = new_ver
                    save_project_config(root, cfg)
                    self.after(0, lambda v=new_ver:
                               self._build_log(f"  Version → {v}", "ok"))
                    self._log.info("Version bumped to %s", new_ver)

            except Exception as exc:
                import traceback
                msg = str(exc)
                tb  = traceback.format_exc()
                self._log.error("Build failed: %s\n%s", msg, tb)
                self.after(0, lambda m=msg: self._build_log(f"ERROR: {m}", "error"))

        threading.Thread(target=_do_build, daemon=True).start()

    def _run_clean_only(self) -> None:
        """Run just the cleaner, report results."""
        if not self.graph:
            self._build_log("No project loaded.", "warn")
            return
        root = self.graph["meta"]["root"]
        self._build_log("── Cleaning ──", "dim")

        def _do_clean():
            try:
                from build.cleaner import clean_project, CleanOptions
                tiers = ["cache", "logs", "build", "dev"]
                report = clean_project(root, CleanOptions(tiers=tiers, verbose=False))
                summary = report.summary()
                self.after(0, lambda s=summary: self._build_log(s, "ok"))
                for removed in report.removed[:20]:
                    self.after(0, lambda r=removed: self._build_log(f"  removed: {r}", "dim"))
                if len(report.removed) > 20:
                    self.after(0, lambda n=len(report.removed)-20:
                               self._build_log(f"  ... and {n} more", "dim"))
                for err in report.errors:
                    self.after(0, lambda e=err: self._build_log(f"  ERR: {e}", "error"))
            except Exception as exc:
                self.after(0, lambda m=str(exc): self._build_log(f"ERROR: {m}", "error"))

        threading.Thread(target=_do_clean, daemon=True).start()

    def _show_build_history(self) -> None:
        """Show last few builds from dist/build-manifest.json."""
        if not self.graph:
            return
        manifest_path = os.path.join(self.graph["meta"]["root"], "dist", "build-manifest.json")
        if not os.path.isfile(manifest_path):
            self._build_log("No build history yet.", "dim")
            return
        try:
            with open(manifest_path) as f:
                data = json.load(f)
            history = data.get("history", [])
            if not history:
                self._build_log("No build history yet.", "dim")
                return
            self._build_log("── Previous builds ──", "dim")
            for b in history[:5]:
                ts    = b.get("built_at", "?")[:16].replace("T", " ")
                kind  = b.get("kind", "?")
                ver   = b.get("version", "?")
                errs  = len(b.get("errors", []))
                level = "warn" if errs else "ok"
                self._build_log(
                    f"  {ts}  v{ver}  {kind}"
                    + (f"  ({errs} warning(s))" if errs else ""),
                    level
                )
        except Exception as exc:
            self._build_log(f"Could not read build history: {exc}", "warn")

    def _build_plan_panel(self, parent):
        """Build the plan panel UI."""
        self._plan_text = tk.Text(parent, bg=P["bg1"], fg=P["t1"], font=self._mono_s,
                                 padx=15, pady=15, borderwidth=0, highlightthickness=0)
        self._plan_text.pack(fill="both", expand=True)
        # Configure tags for plan status
        self._plan_text.tag_config("done", foreground=P["green"])
        self._plan_text.tag_config("doing", foreground=P["amber"])
        self._plan_text.tag_config("todo", foreground=P["t2"])
        self._plan_text.tag_config("header", font=self._mono_m, foreground=P["t0"])

    def _refresh_plan(self):
        """Reload plan from .side/task.md and render it."""
        if not self._plan_text: return
        path = os.path.join(self.project_root, ".side", "task.md")
        if not os.path.isfile(path): path = os.path.join(self.project_root, "task.md")
        if not os.path.isfile(path):
            self._plan_text.config(state="normal")
            self._plan_text.delete("1.0", "end")
            self._plan_text.insert("end", "No task.md found.\nAI can create one using 'create_plan'.")
            self._plan_text.config(state="disabled")
            return
            
        try:
            with open(path, "r") as f: content = f.read()
            self._plan_text.config(state="normal")
            self._plan_text.delete("1.0", "end")
            for line in content.splitlines():
                if line.startswith("#"):
                    self._plan_text.insert("end", line + "\n", "header")
                elif "[x]" in line:
                    self._plan_text.insert("end", " ✓ " + line.replace("[x]", "").strip() + "\n", "done")
                elif "[/]" in line:
                    self._plan_text.insert("end", " ◉ " + line.replace("[/]", "").strip() + "\n", "doing")
                elif "[ ]" in line:
                    self._plan_text.insert("end", " ○ " + line.replace("[ ]", "").strip() + "\n", "todo")
                else:
                    self._plan_text.insert("end", line + "\n")
            self._plan_text.config(state="disabled")
        except Exception: pass

    def _build_playground_panel(self, parent):
        """Build the playground panel UI."""
        top = tk.Frame(parent, bg=P["bg2"], height=40)
        top.pack(fill="x")
        btn = tk.Label(top, text=" RUN CODE ", bg=P["green"], fg="#000", font=self._mono_s, 
                       padx=10, pady=4, cursor="hand2")
        btn.pack(side="right", padx=10, pady=5)
        btn.bind("<Button-1>", lambda _: self._playground_run())
        
        paned = tk.PanedWindow(parent, orient="vertical", bg=P["bg0"], borderwidth=0, sashwidth=2)
        paned.pack(fill="both", expand=True)
        
        self._play_text = tk.Text(paned, bg=P["bg1"], fg=P["t1"], font=self._mono_s,
                                 padx=10, pady=10, borderwidth=0, highlightthickness=0)
        paned.add(self._play_text, height=300)
        
        self._play_out = tk.Text(paned, bg=P["bg0"], fg=P["t2"], font=self._mono_xs,
                                padx=10, pady=10, borderwidth=0, highlightthickness=0)
        self._play_out.tag_config("warn", foreground=P["red"])
        paned.add(self._play_out)

    def _playground_run(self):
        """Execute the code in the playground."""
        if not self._play_text or not self._play_out: return
        code = self._play_text.get("1.0", "end-1c")
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self._play_out.insert("end", f"\n-- [{ts}] Running --\n")
        
        # Save to temp file and run
        tmp = os.path.join(self.project_root, ".side", "playground_scratch.py")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w") as f: f.write(code)
        
        try:
            import subprocess
            res = subprocess.run([sys.executable, tmp], capture_output=True, text=True, timeout=10)
            if res.stdout: self._play_out.insert("end", res.stdout)
            if res.stderr: self._play_out.insert("end", res.stderr, "warn")
        except Exception as e:
            self._play_out.insert("end", f"Error: {e}\n", "warn")
        self._play_out.see("end")


def main():
    app = SIDE_App()
    if len(sys.argv) > 1:
        app._load_project(sys.argv[1])
    else:
        app._load_project(os.getcwd())
    app.mainloop()

if __name__ == "__main__":
    main()
