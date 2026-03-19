"""
gui/canvas_mixin.py
===================
CanvasMixin — all canvas rendering, node/edge drawing, minimap, viewport
transforms, hit-testing, input events, and filter/search logic.

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
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox
from typing import Optional, Any, Union, List, Dict, Set, Tuple
from dataclasses import dataclass, field
from parser.project_parser import parse_project
try:
    from .app import P, CAT, EDGE_STYLES, EDGE_DEFAULT, NW, NH_HEADER, NH_PAD, NH_TAG_ROW, NH_DEF_ROW, NH_EXP_ROW, MAX_DEFS, KIND_ICON, cat_style, edge_style, node_height, fmt_size, _ROOT_DIR
except (ImportError, ValueError):
    from gui.app import P, CAT, EDGE_STYLES, EDGE_DEFAULT, NW, NH_HEADER, NH_PAD, NH_TAG_ROW, NH_DEF_ROW, NH_EXP_ROW, MAX_DEFS, KIND_ICON, cat_style, edge_style, node_height, fmt_size, _ROOT_DIR


class CanvasMixin:
    """Canvas rendering, viewport, hit-testing, input events, filter/search."""

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

        if getattr(self, 'canvas_mode', 'graph') == 'teams':
            self._draw_team_canvas()
            self._draw_minimap()
            self._render_times.append({'ts': time.time(), 'total': 0,
                'clear': 0, 'grid': 0, 'edges': 0, 'nodes': 0, 'minimap': 0,
                'n_nodes': len(self._tw_nodes), 'n_edges': len(self._tw_edges)})
            if len(self._render_times) > 120:
                self._render_times = self._render_times[-120:]
            return

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
            and (n.get("category") not in self.hidden_cats
                 or n.get("category") in self.filter_cats)
            and (not self.filter_cats
                 or n.get("category") in self.filter_cats)
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

            # Changed-node highlight (written during bake/team session)
            if nid in getattr(self, '_changed_nodes', set()):
                c.create_rectangle(sx - 3, sy - 3, sx + sw + 3, sy + sh + 3,
                                    outline=P["green"], width=2,
                                    fill="", tags=("node", f"n:{nid}"))

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

    def _draw_doc_links(self, c, node_map: dict) -> None:
        if "docs" in self.hidden_cats and "docs" not in self.filter_cats:
            return
        if self.vp_z < 0.25:
            return
        for node in self._vis_nodes():
            if node.get("category") != "docs":
                continue
            doc_dir = os.path.dirname(node.get("path", ""))
            doc_id  = node["id"]
            if doc_id not in self.positions:
                continue
            dx, dy = self._npos(doc_id)
            sx, sy = self._w2s(dx + NW / 2, dy + node_height(node) / 2)
            for other in self._vis_nodes():
                if other["id"] == doc_id or other.get("category") == "docs":
                    continue
                if _os.path.dirname(other.get("path", "")) != doc_dir:
                    continue
                if other["id"] not in self.positions:
                    continue
                ox, oy = self._npos(other["id"])
                ex, ey = self._w2s(ox + NW / 2, oy + node_height(other) / 2)
                c.create_line(sx, sy, ex, ey, fill=P["t3"],
                              width=1, dash=(3, 6),
                              tags=("edge", "doclink"))

    def _draw_edges(self):
        c = self._canvas
        node_map = {n["id"]: n for n in self._vis_nodes()}
        self._draw_doc_links(c, node_map)
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
        if getattr(self, 'canvas_mode', 'graph') == 'teams':
            self._tw_canvas_click(event); return
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
        if getattr(self, 'canvas_mode', 'graph') == 'teams':
            self._tw_canvas_drag(event); return
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
        if getattr(self, 'canvas_mode', 'graph') == 'teams':
            self._tw_canvas_release(event); return
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

    def _toggle_filter_cat(self, cat_key: str) -> None:
        """Toggle a category chip. Empty string = ALL (clear)."""
        if cat_key == "":
            self.filter_cats.clear()
            self.hidden_cats = {"docs", "config"}
        elif cat_key in self.filter_cats:
            self.filter_cats.discard(cat_key)
            if cat_key in ("docs", "config"):
                self.hidden_cats.add(cat_key)
        else:
            self.filter_cats.add(cat_key)
            self.hidden_cats.discard(cat_key)
        self._update_cat_chips()
        self._invalidate_cache()
        self._redraw()

    def _set_filter_cat(self, cat_key):
        self._toggle_filter_cat(cat_key)

    def _update_cat_chips(self):
        for key, btn in self._cat_btns.items():
            if key == "":
                active = not self.filter_cats
            else:
                active = key in self.filter_cats
            dim = key in ("docs", "config") and key in self.hidden_cats
            btn.config(
                bg=P["bg4"] if active else P["bg3"],
                fg=P["t0"] if active else (P["t3"] if dim else P["t2"]),
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
                self._log.error("Parse failed: %s\n%s", exc, traceback.format_exc())
                self.after(0, lambda e=exc: self._on_parse_error(str(e)))

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

