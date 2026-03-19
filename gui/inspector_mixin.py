"""
gui/inspector_mixin.py
======================
InspectorMixin — slide-in inspector panel for node detail.

Extracted from gui/app.py. Designed as a mixin for SIDE_App.
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
try:
    from .app import P
except (ImportError, ValueError):
    from gui.app import P


class InspectorMixin:
    """Inspector panel: open, populate, and close the node detail sidebar."""

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

