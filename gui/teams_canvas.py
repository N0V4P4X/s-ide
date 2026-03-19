"""
gui/teams_canvas.py
===================
AI Teams workflow designer — canvas mode for building multi-agent pipelines.

When active, the main canvas switches from showing the project graph to
showing a workflow graph: Agent nodes connected by sequencing edges,
with a toolbar for adding agents, configuring them, and running the workflow.

Architecture
------------
TeamsCanvas owns:
  - _tw_nodes: list[dict]   — agent node data (role, model, name, position)
  - _tw_edges: list[dict]   — sequence edges (source → target)
  - _tw_running: bool       — True while a TeamSession is executing

It is NOT a separate widget — it reuses the existing canvas and extends
SIDE_App with teams-mode drawing and interaction methods.

Integration points in app.py:
  - self.canvas_mode: str = "graph" | "teams"
  - "Teams" button in topbar → toggle canvas_mode
  - _do_redraw routes to _draw_team_canvas() when canvas_mode == "teams"
  - canvas click/drag routes to team-mode handlers

Node shape (teams mode)
-----------------------
Each agent card is 200px wide × 120px tall, hexagonal header with role colour.
Layout: left-to-right chain by default (user can drag to rearrange).

Visual conventions:
  role colours mirror the existing CAT palette:
    architect    → blue (P["blue"])
    implementer  → green (P["green"])
    reviewer     → amber (P["amber"])
    tester       → cyan (P["cyan"])
    optimizer    → purple (P["purple"])
    documentarian → pink (P["pink"])
"""

from __future__ import annotations
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass   # avoid circular — app imports this module

# tkinter imported lazily inside GUI methods so this module
# can be imported in headless test environments
try:
    import tkinter as tk
    import tkinter.ttk as ttk
except ImportError:
    tk = None   # type: ignore
    ttk = None  # type: ignore

# ── Constants ─────────────────────────────────────────────────────────────────

TW  = 200    # team node width (canvas units)
TH  = 110    # team node height
THH = 36     # team node header height
TGX = 260    # horizontal gap between nodes
TGY = 140    # vertical gap (for branching, future)

ROLE_COLOURS = {
    "architect":     "#4da6ff",   # blue
    "implementer":   "#39ff8a",   # phosphor green
    "reviewer":      "#ffaa33",   # amber
    "tester":        "#33ddcc",   # cyan
    "optimizer":     "#aa66ff",   # purple
    "documentarian": "#ff55aa",   # pink
}
ROLE_DARK = {
    "architect":     "#1a3a5a",
    "implementer":   "#1a3a20",
    "reviewer":      "#3a2a00",
    "tester":        "#003a34",
    "optimizer":     "#2a1a4a",
    "documentarian": "#3a0022",
}

DEFAULT_MODELS = ["llama3.2", "codellama", "mistral", "phi3", "gemma2"]


# ── Teams canvas mixin ────────────────────────────────────────────────────────

class TeamsCanvasMixin:
    """
    Mixed into SIDE_App. Provides all Teams-mode canvas methods.

    State attributes (initialised by _teams_init):
      self._tw_nodes: list[dict]   each: {id, role, model, name, x, y}
      self._tw_edges: list[dict]   each: {id, source, target}
      self._tw_sel: str | None     selected node id
      self._tw_drag: dict | None   drag state
      self._tw_running: bool
      self._tw_session_id: str | None
      self.canvas_mode: str        "graph" | "teams"
    """

    # ── Initialisation ─────────────────────────────────────────────────────────

    def _teams_init(self) -> None:
        """Call from SIDE_App.__init__ after other state is set."""
        self._tw_nodes: list[dict] = []
        self._tw_edges: list[dict] = []
        self._tw_sel: str | None = None
        self._tw_drag: dict | None = None
        self._tw_running: bool = False
        self._tw_session_id: str | None = None
        self.canvas_mode: str = "graph"
        self._tw_node_counter: int = 0
        self._tw_available_models: list[str] = list(DEFAULT_MODELS)

    # ── Mode toggle ────────────────────────────────────────────────────────────

    def _toggle_teams_mode(self) -> None:
        """Switch between graph view and teams designer."""
        from gui.app import P
        if self.canvas_mode == "graph":
            self.canvas_mode = "teams"
            self._teams_btn.config(fg=P["green"],
                                   highlightbackground=P["green2"])
            if not self._tw_nodes:
                self._tw_add_default_workflow()
        else:
            self.canvas_mode = "graph"
            self._teams_btn.config(fg=P["t2"],
                                   highlightbackground=P["line2"])
        self._tw_sel = None
        self._redraw()


    # ── Default workflow ────────────────────────────────────────────────────────

    def _tw_add_default_workflow(self) -> None:
        """Seed a starter workflow: Architect → Implementer → Reviewer → Tester."""
        defaults = ["architect", "implementer", "reviewer", "tester"]
        prev_id = None
        for i, role in enumerate(defaults):
            nid = self._tw_new_node(role, x=80 + i * TGX, y=80)
            if prev_id:
                self._tw_edges.append({
                    "id": f"te_{prev_id}_{nid}",
                    "source": prev_id,
                    "target": nid,
                })
            prev_id = nid

    # ── Node management ─────────────────────────────────────────────────────────

    def _tw_new_node(self, role: str, x: float = 80, y: float = 80) -> str:
        self._tw_node_counter += 1
        nid = f"tw_{self._tw_node_counter}"
        self._tw_nodes.append({
            "id":    nid,
            "role":  role,
            "model": self._tw_available_models[0] if self._tw_available_models else "llama3.2",
            "name":  role.title(),
            "x":     float(x),
            "y":     float(y),
        })
        return nid

    def _tw_delete_node(self, nid: str) -> None:
        self._tw_nodes = [n for n in self._tw_nodes if n["id"] != nid]
        self._tw_edges = [e for e in self._tw_edges
                          if e["source"] != nid and e["target"] != nid]
        if self._tw_sel == nid:
            self._tw_sel = None

    def _tw_node_by_id(self, nid: str) -> dict | None:
        return next((n for n in self._tw_nodes if n["id"] == nid), None)

    # ── Drawing ────────────────────────────────────────────────────────────────

    def _draw_team_canvas(self) -> None:
        """Draw the full teams workflow canvas."""
        from gui.app import P
        c = self._canvas

        self._draw_grid()   # reuse grid

        # Draw edges (sequence arrows)
        self._tw_draw_edges(c, P)

        # Draw agent nodes
        for node in self._tw_nodes:
            self._tw_draw_node(c, node, P)

        # Draw "add agent" button at the end of the chain
        self._tw_draw_add_btn(c, P)

        # Rebuild hit boxes
        self._tw_rebuild_hit_boxes()

    def _tw_draw_edges(self, c: tk.Canvas, P: dict) -> None:
        """Draw sequence arrows between agent nodes."""
        for edge in self._tw_edges:
            src = self._tw_node_by_id(edge["source"])
            tgt = self._tw_node_by_id(edge["target"])
            if not src or not tgt:
                continue
            sx, sy = self._w2s(src["x"] + TW, src["y"] + TH / 2)
            tx, ty = self._w2s(tgt["x"], tgt["y"] + TH / 2)
            mid_x = (sx + tx) / 2
            c.create_line(sx, sy, mid_x, sy, mid_x, ty, tx, ty,
                          fill=P["line3"], width=2 * self.vp_z,
                          smooth=True, arrow="last",
                          arrowshape=(10 * self.vp_z, 12 * self.vp_z, 4 * self.vp_z),
                          tags="tw_edge")

    def _tw_draw_node(self, c: tk.Canvas, node: dict, P: dict) -> None:
        """Draw one agent node card."""
        nid   = node["id"]
        role  = node["role"]
        x, y  = node["x"], node["y"]
        sx, sy = self._w2s(x, y)
        sw    = TW * self.vp_z
        sh    = TH * self.vp_z
        shh   = THH * self.vp_z

        accent = ROLE_COLOURS.get(role, P["t2"])
        dark   = ROLE_DARK.get(role, P["bg2"])
        is_sel = nid == self._tw_sel

        # Card bg
        c.create_rectangle(sx, sy, sx + sw, sy + sh,
                            fill=P["bg2"], outline=accent if is_sel else P["line2"],
                            width=2 if is_sel else 1,
                            tags=("tw_node", f"tw:{nid}"))

        # Selection glow
        if is_sel:
            c.create_rectangle(sx - 2, sy - 2, sx + sw + 2, sy + sh + 2,
                                outline=accent, width=1, fill="",
                                tags=("tw_node", f"tw:{nid}"))

        # Header band
        c.create_rectangle(sx, sy, sx + sw, sy + shh,
                            fill=dark, outline="",
                            tags=("tw_node", f"tw:{nid}"))

        if self.vp_z > 0.3:
            mono = self._mono.actual()["family"]
            z = self.vp_z

            # Role accent dot
            dot_r = 5 * z
            c.create_oval(sx + 10*z, sy + shh/2 - dot_r,
                           sx + 10*z + dot_r*2, sy + shh/2 + dot_r,
                           fill=accent, outline="", tags=("tw_node", f"tw:{nid}"))

            # Role name (header)
            c.create_text(sx + 24*z, sy + shh/2,
                           text=node["name"],
                           anchor="w", fill=P["t0"],
                           font=(mono, max(7, int(10*z)), "bold"),
                           tags=("tw_node", f"tw:{nid}"))

            # Role badge (top-right)
            c.create_text(sx + sw - 6*z, sy + shh/2,
                           text=role.upper(),
                           anchor="e", fill=accent,
                           font=(mono, max(5, int(7*z))),
                           tags=("tw_node", f"tw:{nid}"))

            # Body content
            body_y = sy + shh + 8*z
            # Model
            c.create_text(sx + 10*z, body_y,
                           text=f"model: {node['model']}",
                           anchor="nw", fill=P["t2"],
                           font=(mono, max(6, int(8*z))),
                           tags=("tw_node", f"tw:{nid}"))

            # Running indicator
            if self._tw_running and self._tw_session_id:
                c.create_text(sx + sw/2, sy + sh - 8*z,
                               text="● running",
                               anchor="s", fill=accent,
                               font=(mono, max(6, int(8*z))),
                               tags=("tw_node", f"tw:{nid}"))

    def _tw_draw_add_btn(self, c: tk.Canvas, P: dict) -> None:
        """Draw + button to the right of the last node."""
        if not self._tw_nodes:
            # Draw centred if empty
            sx, sy = self._w2s(80, 80)
        else:
            rightmost = max(self._tw_nodes, key=lambda n: n["x"])
            sx, sy = self._w2s(rightmost["x"] + TW + 30, rightmost["y"] + TH/2 - 18)

        r = 18 * self.vp_z
        c.create_oval(sx, sy, sx + r*2, sy + r*2,
                       fill=P["bg3"], outline=P["line3"], width=1,
                       tags="tw_add_btn")
        c.create_text(sx + r, sy + r,
                       text="+", anchor="center",
                       fill=P["t2"],
                       font=(self._mono.actual()["family"], max(8, int(14 * self.vp_z))),
                       tags="tw_add_btn")

    # ── Hit boxes ──────────────────────────────────────────────────────────────

    def _tw_rebuild_hit_boxes(self) -> None:
        self._tw_hit_boxes: dict = {}
        for node in self._tw_nodes:
            sx, sy = self._w2s(node["x"], node["y"])
            ex, ey = self._w2s(node["x"] + TW, node["y"] + TH)
            self._tw_hit_boxes[node["id"]] = (sx, sy, ex, ey)
        # Add button hit box
        if self._tw_nodes:
            rightmost = max(self._tw_nodes, key=lambda n: n["x"])
            ax, ay = self._w2s(rightmost["x"] + TW + 30, rightmost["y"] + TH/2 - 18)
            r = 18 * self.vp_z
            self._tw_hit_boxes["__add__"] = (ax, ay, ax + r*2, ay + r*2)
        else:
            ax, ay = self._w2s(80, 80)
            r = 18 * self.vp_z
            self._tw_hit_boxes["__add__"] = (ax, ay, ax + r*2, ay + r*2)

    def _tw_hit_test(self, sx: float, sy: float) -> str | None:
        """Return node id (or '__add__') at screen coords, or None."""
        boxes = getattr(self, "_tw_hit_boxes", {})
        for nid, (x0, y0, x1, y1) in boxes.items():
            if x0 <= sx <= x1 and y0 <= sy <= y1:
                return nid
        return None

    # ── Canvas event handlers (teams mode) ─────────────────────────────────────

    def _tw_canvas_click(self, event) -> None:
        hit = self._tw_hit_test(event.x, event.y)
        if hit == "__add__":
            self._tw_show_add_dialog()
            return
        if hit:
            self._tw_sel = hit
            wx, wy = self._s2w(event.x, event.y)
            n = self._tw_node_by_id(hit)
            self._tw_drag = {
                "id": hit,
                "ox": n["x"], "oy": n["y"],
                "sx": event.x, "sy": event.y,
            }
        else:
            self._tw_sel = None
            self._pan = {"sx": event.x, "sy": event.y,
                         "ox": self.vp_x, "oy": self.vp_y}
        self._redraw()

    def _tw_canvas_drag(self, event) -> None:
        if self._tw_drag:
            d = self._tw_drag
            dwx = (event.x - d["sx"]) / self.vp_z
            dwy = (event.y - d["sy"]) / self.vp_z
            n = self._tw_node_by_id(d["id"])
            if n:
                n["x"] = d["ox"] + dwx
                n["y"] = d["oy"] + dwy
            self._redraw()
        elif self._pan:
            p = self._pan
            self.vp_x = p["ox"] + (event.x - p["sx"])
            self.vp_y = p["oy"] + (event.y - p["sy"])
            self._apply_vp()

    def _tw_canvas_release(self, event) -> None:
        self._tw_drag = None
        self._pan = None

    def _tw_canvas_double_click(self, event) -> None:
        hit = self._tw_hit_test(event.x, event.y)
        if hit and hit != "__add__":
            self._tw_edit_node(hit)

    def _tw_canvas_right_click(self, event) -> None:
        from gui.app import P
        hit = self._tw_hit_test(event.x, event.y)
        if not hit or hit == "__add__":
            return
        node = self._tw_node_by_id(hit)
        if not node:
            return
        menu = tk.Menu(self, tearoff=0,
                       bg=P["bg2"], fg=P["t1"],
                       activebackground=P["bg3"], activeforeground=P["t0"],
                       bd=0, relief="flat", font=self._mono_xs)
        menu.add_command(label=f"  {node['name']} ({node['role']})",
                         state="disabled", font=self._mono_s)
        menu.add_separator()
        menu.add_command(label="  ✎  Configure",
                         command=lambda: self._tw_edit_node(hit))
        menu.add_command(label="  ✕  Remove",
                         command=lambda: (self._tw_delete_node(hit), self._redraw()))
        menu.add_separator()
        menu.add_command(label="  ▦  Templates",
                         command=lambda: self._tw_show_template_dialog())
        if self._tw_nodes and self._tw_nodes[-1]["id"] != hit:
            menu.add_separator()
            menu.add_command(label="  → Connect to next",
                             command=lambda: self._tw_auto_connect())
        menu.tk_popup(event.x_root, event.y_root)

    # ── Dialogs ────────────────────────────────────────────────────────────────

    def _tw_show_add_dialog(self) -> None:
        """Dialog to add a new agent to the workflow."""
        from gui.app import P
        win = tk.Toplevel(self)
        win.title("Add Agent")
        win.configure(bg=P["bg1"])
        win.geometry("340x280")
        win.resizable(False, False)
        win.transient(self)

        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")
        hdr = tk.Frame(win, bg=P["bg2"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="ADD AGENT", bg=P["bg2"], fg=P["t0"],
                 font=self._mono_l, padx=14, pady=10).pack(anchor="w")
        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")

        body = tk.Frame(win, bg=P["bg1"])
        body.pack(fill="both", expand=True, padx=16, pady=12)

        def _row(label, widget_fn):
            r = tk.Frame(body, bg=P["bg1"])
            r.pack(fill="x", pady=4)
            tk.Label(r, text=label, bg=P["bg1"], fg=P["t2"],
                     font=self._mono_xs, width=10, anchor="w").pack(side="left")
            return widget_fn(r)

        roles = ["architect", "implementer", "reviewer",
                 "tester", "optimizer", "documentarian"]
        role_var  = tk.StringVar(value="implementer")
        model_var = tk.StringVar(
            value=self._tw_available_models[0] if self._tw_available_models else "llama3.2")
        name_var  = tk.StringVar(value="")

        def _update_name(*_):
            if not name_var.get() or name_var.get() == prev_role[0].title():
                name_var.set(role_var.get().title())
            prev_role[0] = role_var.get()
        prev_role = [role_var.get()]
        role_var.trace_add("write", _update_name)
        name_var.set(role_var.get().title())

        _row("Role",  lambda p: ttk.Combobox(p, textvariable=role_var,
                                              values=roles, width=18,
                                              font=self._mono_xs, state="readonly")).pack(side="left")
        _row("Model", lambda p: ttk.Combobox(p, textvariable=model_var,
                                              values=self._tw_available_models or DEFAULT_MODELS,
                                              width=18, font=self._mono_xs)).pack(side="left")
        _row("Name",  lambda p: tk.Entry(p, textvariable=name_var,
                                          bg=P["bg3"], fg=P["t0"], bd=0,
                                          insertbackground=P["green"],
                                          font=self._mono_s, width=20)).pack(side="left")

        tk.Frame(body, bg=P["line"], height=1).pack(fill="x", pady=8)
        btn_row = tk.Frame(body, bg=P["bg1"])
        btn_row.pack(fill="x")

        def _add():
            # Find a good position: to the right of the last node
            if self._tw_nodes:
                last = max(self._tw_nodes, key=lambda n: n["x"])
                x = last["x"] + TGX
                y = last["y"]
                prev_id = last["id"]
            else:
                x, y = 80.0, 80.0
                prev_id = None
            nid = self._tw_new_node(role_var.get(), x=x, y=y)
            n = self._tw_node_by_id(nid)
            if n:
                n["model"] = model_var.get()
                n["name"]  = name_var.get() or role_var.get().title()
            if prev_id:
                self._tw_edges.append({
                    "id": f"te_{prev_id}_{nid}",
                    "source": prev_id,
                    "target": nid,
                })
            win.destroy()
            self._redraw()

        add_btn = tk.Label(btn_row, text="Add", bg=P["green2"], fg=P["green"],
                           font=self._mono_s, padx=12, pady=4, cursor="hand2",
                           highlightbackground=P["green"], highlightthickness=1)
        add_btn.pack(side="left")
        add_btn.bind("<Button-1>", lambda _: _add())

        cancel_btn = tk.Label(btn_row, text="Cancel", bg=P["bg3"], fg=P["t2"],
                              font=self._mono_xs, padx=8, pady=4, cursor="hand2",
                              highlightbackground=P["line2"], highlightthickness=1)
        cancel_btn.pack(side="right")
        cancel_btn.bind("<Button-1>", lambda _: win.destroy())

        win.bind("<Return>", lambda _: _add())
        win.bind("<Escape>", lambda _: win.destroy())

    def _tw_edit_node(self, nid: str) -> None:
        """Edit an existing agent node."""
        from gui.app import P
        node = self._tw_node_by_id(nid)
        if not node:
            return

        win = tk.Toplevel(self)
        win.title(f"Configure — {node['name']}")
        win.configure(bg=P["bg1"])
        win.geometry("340x220")
        win.resizable(False, False)
        win.transient(self)

        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")
        hdr = tk.Frame(win, bg=P["bg2"])
        hdr.pack(fill="x")
        accent = ROLE_COLOURS.get(node["role"], P["t2"])
        tk.Label(hdr, text=f"  {node['role'].upper()}", bg=P["bg2"], fg=accent,
                 font=self._mono_l, padx=14, pady=10).pack(anchor="w")
        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")

        body = tk.Frame(win, bg=P["bg1"])
        body.pack(fill="both", expand=True, padx=16, pady=12)

        def _row(label, widget_fn):
            r = tk.Frame(body, bg=P["bg1"])
            r.pack(fill="x", pady=4)
            tk.Label(r, text=label, bg=P["bg1"], fg=P["t2"],
                     font=self._mono_xs, width=10, anchor="w").pack(side="left")
            return widget_fn(r)

        model_var = tk.StringVar(value=node["model"])
        name_var  = tk.StringVar(value=node["name"])

        _row("Model", lambda p: ttk.Combobox(
            p, textvariable=model_var,
            values=self._tw_available_models or DEFAULT_MODELS,
            width=18, font=self._mono_xs)).pack(side="left")
        _row("Name",  lambda p: tk.Entry(
            p, textvariable=name_var, bg=P["bg3"], fg=P["t0"], bd=0,
            insertbackground=P["green"],
            font=self._mono_s, width=20)).pack(side="left")

        tk.Frame(body, bg=P["line"], height=1).pack(fill="x", pady=8)
        btn_row = tk.Frame(body, bg=P["bg1"])
        btn_row.pack(fill="x")

        def _save():
            node["model"] = model_var.get()
            node["name"]  = name_var.get() or node["role"].title()
            win.destroy()
            self._redraw()

        save_btn = tk.Label(btn_row, text="Save", bg=P["green2"], fg=P["green"],
                            font=self._mono_s, padx=12, pady=4, cursor="hand2",
                            highlightbackground=P["green"], highlightthickness=1)
        save_btn.pack(side="left")
        save_btn.bind("<Button-1>", lambda _: _save())

        cancel_btn = tk.Label(btn_row, text="Cancel", bg=P["bg3"], fg=P["t2"],
                              font=self._mono_xs, padx=8, pady=4, cursor="hand2",
                              highlightbackground=P["line2"], highlightthickness=1)
        cancel_btn.pack(side="right")
        cancel_btn.bind("<Button-1>", lambda _: win.destroy())

        win.bind("<Return>", lambda _: _save())
        win.bind("<Escape>", lambda _: win.destroy())

    def _tw_auto_connect(self) -> None:
        """Connect nodes left-to-right by x position."""
        self._tw_edges.clear()
        sorted_nodes = sorted(self._tw_nodes, key=lambda n: n["x"])
        for i in range(len(sorted_nodes) - 1):
            src = sorted_nodes[i]["id"]
            tgt = sorted_nodes[i + 1]["id"]
            self._tw_edges.append({
                "id": f"te_{src}_{tgt}",
                "source": src,
                "target": tgt,
            })
        self._redraw()

    # ── Workflow execution ─────────────────────────────────────────────────────

    def _tw_show_template_dialog(self) -> None:
        """Show dialog to load a saved template or save the current workflow."""
        from gui.app import P
        from ai.workflow_templates import list_templates, save_template, delete_template

        win = tk.Toplevel(self)
        win.title("Workflow Templates")
        win.configure(bg=P["bg1"])
        win.geometry("400x420")
        win.resizable(True, True)
        win.transient(self)

        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")
        hdr = tk.Frame(win, bg=P["bg2"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="WORKFLOW TEMPLATES", bg=P["bg2"], fg=P["t0"],
                 font=self._mono_l, padx=14, pady=10).pack(anchor="w")
        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")

        # Template list
        lst_f = tk.Frame(win, bg=P["bg0"])
        lst_f.pack(fill="both", expand=True, padx=10, pady=8)
        sb = tk.Scrollbar(lst_f); sb.pack(side="right", fill="y")
        lb = tk.Listbox(lst_f, bg=P["bg0"], fg=P["t1"],
                        font=(self._mono_xs.actual()["family"], 9),
                        bd=0, highlightthickness=0,
                        selectbackground=P["bg3"],
                        yscrollcommand=sb.set, activestyle="none")
        lb.pack(fill="both", expand=True)
        sb.config(command=lb.yview)

        desc_var = tk.StringVar(value="")
        tk.Label(win, textvariable=desc_var, bg=P["bg1"], fg=P["t2"],
                 font=(self._mono_xs.actual()["family"], 8),
                 wraplength=360, justify="left", padx=10).pack(anchor="w")

        templates = list_templates()
        for t in templates:
            tag = "[builtin] " if t.builtin else ""
            lb.insert("end", f"{tag}{t.name}")

        def _on_select(_=None):
            sel = lb.curselection()
            if sel and sel[0] < len(templates):
                desc_var.set(templates[sel[0]].description)
        lb.bind("<<ListboxSelect>>", _on_select)

        # Buttons
        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")
        btn_row = tk.Frame(win, bg=P["bg1"])
        btn_row.pack(fill="x", padx=10, pady=8)

        def _load():
            sel = lb.curselection()
            if not sel: return
            t = templates[sel[0]]
            nodes, edges = t.to_canvas_nodes()
            self._tw_nodes.clear()
            self._tw_edges.clear()
            self._tw_node_counter = 0
            # Re-create nodes with proper IDs
            id_map = {}
            for n in nodes:
                nid = self._tw_new_node(n['role'], x=n['x'], y=n['y'])
                node = self._tw_node_by_id(nid)
                if node:
                    node['model'] = n['model']
                    node['name']  = n['name']
                id_map[n['id']] = nid
            for e in edges:
                src = id_map.get(e['source'])
                tgt = id_map.get(e['target'])
                if src and tgt:
                    self._tw_edges.append({
                        'id': f'te_{src}_{tgt}',
                        'source': src, 'target': tgt})
            if self.canvas_mode != 'teams':
                self._toggle_teams_mode()
            win.destroy()
            self._redraw()

        def _save_current():
            if not self._tw_nodes:
                return
            from tkinter.simpledialog import askstring
            name = askstring('Save Template',
                'Template name (no spaces):', parent=win)
            if not name:
                return
            name = name.strip().replace(' ', '_').lower()
            save_template(name, self._tw_nodes, self._tw_edges)
            win.destroy()
            self._tw_show_template_dialog()

        def _delete():
            sel = lb.curselection()
            if not sel: return
            t = templates[sel[0]]
            if t.builtin: return
            delete_template(t.name)
            win.destroy()
            self._tw_show_template_dialog()

        load_btn = tk.Label(btn_row, text='Load', bg=P['green2'], fg=P['green'],
                            font=self._mono_s, padx=12, pady=4, cursor='hand2',
                            highlightbackground=P['green'], highlightthickness=1)
        load_btn.pack(side='left')
        load_btn.bind('<Button-1>', lambda _: _load())

        save_btn = tk.Label(btn_row, text='Save current', bg=P['bg3'], fg=P['t2'],
                            font=self._mono_xs, padx=8, pady=4, cursor='hand2',
                            highlightbackground=P['line2'], highlightthickness=1)
        save_btn.pack(side='left', padx=6)
        save_btn.bind('<Button-1>', lambda _: _save_current())

        del_btn = tk.Label(btn_row, text='Delete', bg=P['bg3'], fg=P['red'],
                           font=self._mono_xs, padx=8, pady=4, cursor='hand2',
                           highlightbackground=P['line2'], highlightthickness=1)
        del_btn.pack(side='right')
        del_btn.bind('<Button-1>', lambda _: _delete())

        win.bind('<Return>', lambda _: _load())
        win.bind('<Escape>', lambda _: win.destroy())

    def _tw_run_workflow(self) -> None:
        """Build a TeamSession from current nodes and run it."""
        if self._tw_running:
            return
        if not self._tw_nodes:
            from tkinter import messagebox
            messagebox.showinfo("Teams", "Add at least one agent first.")
            return
        if not self.graph:
            from tkinter import messagebox
            messagebox.showinfo("Teams", "Load a project first.")
            return

        # Get task from the Plan tab if available
        task = self._tw_get_task()
        if not task:
            self._tw_prompt_task()
            return

        self._tw_running = True
        self._redraw()
        # Switch to Teams Log and announce start
        self._select_bottom_tab("teams")
        self._teams_log_append(
            f"{'─'*50}\nWorkflow started — {len(self._tw_nodes)} agent(s)\n"
            f"Task: {task[:80]}\n{'─'*50}\n", "header")

        from ai.teams import TeamSession, AgentConfig

        # Build agent list in edge order
        ordered = self._tw_order_by_edges()
        agents = [
            AgentConfig(
                role  = n["role"],
                model = n["model"],
                name  = n["name"],
            )
            for n in ordered
        ]

        proj_root = self.graph["meta"]["root"]
        session = TeamSession(
            project_root = proj_root,
            task         = task,
            agents       = agents,
            graph        = self.graph,
            on_event     = lambda e: self.after(0, lambda ev=e: self._tw_on_event(ev)),
        )
        self._tw_session_id = session.session_id

        def _run():
            result = session.run()
            self.after(0, lambda r=result: self._tw_on_complete(r))

        threading.Thread(target=_run, daemon=True).start()

    def _tw_order_by_edges(self) -> list[dict]:
        """Topological order of nodes by edges, fallback to x-position."""
        if not self._tw_edges:
            return sorted(self._tw_nodes, key=lambda n: n["x"])
        # Build adjacency
        out_edges = {n["id"]: [] for n in self._tw_nodes}
        in_count  = {n["id"]: 0 for n in self._tw_nodes}
        for e in self._tw_edges:
            if e["source"] in out_edges:
                out_edges[e["source"]].append(e["target"])
            if e["target"] in in_count:
                in_count[e["target"]] += 1
        # Kahn's algorithm
        queue = [nid for nid, c in in_count.items() if c == 0]
        result = []
        while queue:
            nid = queue.pop(0)
            n = self._tw_node_by_id(nid)
            if n:
                result.append(n)
            for tgt in out_edges.get(nid, []):
                in_count[tgt] -= 1
                if in_count[tgt] == 0:
                    queue.append(tgt)
        # Append any nodes not reached (disconnected)
        reached = {n["id"] for n in result}
        for n in sorted(self._tw_nodes, key=lambda n: n["x"]):
            if n["id"] not in reached:
                result.append(n)
        return result

    def _tw_get_task(self) -> str:
        """Get the current task from the plan text widget."""
        w = getattr(self, "_plan_text", None)
        if not w:
            return ""
        try:
            content = w.get("1.0", "2.0").strip()  # first line
            if content and not content.startswith("#"):
                return content
            # Try first non-header line
            for line in w.get("1.0", "end").splitlines():
                line = line.strip().lstrip("# ").strip()
                if line:
                    return line
        except Exception:
            pass
        return ""

    def _tw_prompt_task(self) -> None:
        """Ask the user for a task description."""
        from gui.app import P
        win = tk.Toplevel(self)
        win.title("What should the team work on?")
        win.configure(bg=P["bg1"])
        win.geometry("500x200")
        win.resizable(True, False)
        win.transient(self)

        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")
        tk.Label(win, text="TASK DESCRIPTION", bg=P["bg1"], fg=P["t0"],
                 font=self._mono_l, padx=14, pady=10).pack(anchor="w")
        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")

        task_var = tk.StringVar()
        e = tk.Entry(win, textvariable=task_var, bg=P["bg3"], fg=P["t0"],
                     insertbackground=P["green"], bd=0,
                     font=(self._mono.actual()["family"], 12))
        e.pack(fill="x", padx=16, pady=16, ipady=6)
        e.focus_set()

        btn_row = tk.Frame(win, bg=P["bg1"])
        btn_row.pack(fill="x", padx=16, pady=(0, 16))

        def _run():
            task = task_var.get().strip()
            if task:
                win.destroy()
                # Write to plan tab
                w = getattr(self, "_plan_text", None)
                if w:
                    w.config(state="normal")
                    w.delete("1.0", "end")
                    w.insert("1.0", task)
                    w.config(state="disabled")
                self._tw_run_workflow()

        run_btn = tk.Label(btn_row, text="Run Workflow", bg=P["green2"], fg=P["green"],
                           font=self._mono_s, padx=12, pady=4, cursor="hand2",
                           highlightbackground=P["green"], highlightthickness=1)
        run_btn.pack(side="left")
        run_btn.bind("<Button-1>", lambda _: _run())
        win.bind("<Return>", lambda _: _run())
        win.bind("<Escape>", lambda _: win.destroy())

    def _tw_on_event(self, event) -> None:
        """Route a TeamEvent to Teams Log (full) and AI chat (summaries only)."""
        import time as _time
        from gui.panels import ai_append
        tag = ("tool"  if event.type in ("handoff", "start", "tool") else
               "error" if event.type == "error" else "dim")
        ts = _time.strftime("%H:%M:%S")
        # Always log to Teams Log tab (full verbosity)
        self._teams_log_append(
            f"[{ts}] [{event.agent}] {event.message}\n", tag)
        # Verbose events stay only in Teams Log
        if event.type in ("text", "tool", "tool_result"):
            return
        # Handoff/start/done/error also shown in AI chat
        ai_append(self, f"\n[{event.agent}] {event.message}\n", tag)
        self._redraw()

    def _tw_on_complete(self, result) -> None:
        """Handle workflow completion."""
        self._tw_running = False
        self._tw_session_id = result.session_id
        self._redraw()

        from gui.panels import ai_append
        ai_append(self, f"\n{'─'*40}\n", "dim")
        ai_append(self, f"Workflow complete.\n{result.summary()}\n", "dim")
        ai_append(self, f"\nSession: {result.session_dir}\n", "dim")
        ai_append(self, "Call result.apply() to promote outputs to the project.\n", "dim")

        # Switch to AI tab to show results
        self._select_bottom_tab("ai")

        # Show approval dialog
        self._tw_show_result_dialog(result)

    def _tw_show_result_dialog(self, result) -> None:
        """Show a dialog to review and approve workflow results."""
        from gui.app import P
        win = tk.Toplevel(self)
        win.title("Workflow Complete — Review Results")
        win.configure(bg=P["bg1"])
        win.geometry("500x360")
        win.resizable(True, True)
        win.transient(self)

        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")
        hdr = tk.Frame(win, bg=P["bg2"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="WORKFLOW RESULTS", bg=P["bg2"], fg=P["t0"],
                 font=self._mono_l, padx=14, pady=10).pack(anchor="w")
        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")

        # Summary
        out_f = tk.Frame(win, bg=P["bg0"])
        out_f.pack(fill="both", expand=True)
        sb = tk.Scrollbar(out_f); sb.pack(side="right", fill="y")
        txt = tk.Text(out_f, bg=P["bg0"], fg=P["t1"],
                      font=(self._mono_xs.actual()["family"], 9),
                      yscrollcommand=sb.set, bd=0, state="disabled",
                      wrap="word", padx=12, pady=8)
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)
        txt.tag_config("ok",  foreground=P["green"])
        txt.tag_config("dim", foreground=P["t2"])

        txt.config(state="normal")
        txt.insert("end", result.summary() + "\n\n", "")
        txt.insert("end", f"Session workspace:\n{result.session_dir}\n\n", "dim")
        txt.insert("end", "Apply will promote implementation/ and docs/ to your project.\n", "dim")
        txt.config(state="disabled")

        # Buttons
        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")
        btn_row = tk.Frame(win, bg=P["bg1"])
        btn_row.pack(fill="x", padx=14, pady=10)

        def _approve():
            applied = result.apply()
            txt.config(state="normal")
            txt.insert("end", f"\nApplied {len(applied)} file(s):\n", "ok")
            for f in applied:
                txt.insert("end", f"  {f}\n", "ok")
            txt.config(state="disabled")
            txt.see("end")
            if self.graph:
                self._load_project(self.graph["meta"]["root"])

        approve_btn = tk.Label(btn_row, text="✓ Apply to Project",
                               bg=P["green2"], fg=P["green"],
                               font=self._mono_s, padx=12, pady=4,
                               cursor="hand2",
                               highlightbackground=P["green"], highlightthickness=1)
        approve_btn.pack(side="left")
        approve_btn.bind("<Button-1>", lambda _: _approve())

        open_btn = tk.Label(btn_row, text="Open Session Folder",
                            bg=P["bg3"], fg=P["t2"],
                            font=self._mono_xs, padx=8, pady=4,
                            cursor="hand2",
                            highlightbackground=P["line2"], highlightthickness=1)
        open_btn.pack(side="left", padx=8)
        open_btn.bind("<Button-1>", lambda _: self._open_editor(
            filepath=result.session_dir + "/TASK.md"))

        close_btn = tk.Label(btn_row, text="Close", bg=P["bg3"], fg=P["t2"],
                             font=self._mono_xs, padx=8, pady=4,
                             cursor="hand2",
                             highlightbackground=P["line2"], highlightthickness=1)
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda _: win.destroy())
