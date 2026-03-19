"""
gui/ai_mixin.py
===============
AIMixin — AI assistant panel, Manager integration, streaming markdown,
bake mode, tool-missing dialog, new-project wizard, Teams Log panel,
Plan panel, Playground panel, and session browser.

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
    from ai.teams import list_sessions
    from ai.tool_builder import build_tool_with_team
    from ai.manager import scaffold_new_project
except (ImportError, ValueError):
    from gui.app import P
    from ai.teams import list_sessions
    from ai.tool_builder import build_tool_with_team
    from ai.manager import scaffold_new_project


class AIMixin:
    """AI assistant panel, manager, bake, teams log, plan, playground, session browser."""

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

        # Bake button — starts time-limited autonomous dev session
        self._bake_btn = tk.Label(hi, text='🔥 Bake', bg=P['bg3'], fg=P['t2'],
                                   font=self._mono_xs, padx=6, pady=2, cursor='hand2',
                                   highlightbackground=P['line2'], highlightthickness=1)
        self._bake_btn.pack(side='right', padx=(0, 4))
        self._bake_btn.bind('<Button-1>', lambda _: self._bake_btn_click())
        tk.Frame(hdr, bg=P['line'], height=1).pack(fill='x')

        # Input (pack at bottom first so it stays visible)
        tk.Frame(parent, bg=P['line'], height=1).pack(fill='x', side='bottom')
        inp_f = tk.Frame(parent, bg=P['bg2'])
        inp_f.pack(fill='x', side='bottom', padx=10, pady=8)
        self._ai_input_var = tk.StringVar()
        self._ai_input = tk.Entry(inp_f, textvariable=self._ai_input_var,
                        bg=P['bg3'], fg=P['t0'], insertbackground=P['green'],
                        bd=0, font=(self._mono_xs.actual()['family'], 11), width=50)
        self._ai_input.pack(side='left', fill='x', expand=True, ipady=5, padx=(0, 8))
        self._ai_input.bind('<Return>', lambda _: self._ai_send())
        self._ai_input.bind('<Button-1>', lambda _: self._ai_input.focus_force())
        
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
            err_msg = str(e)
            self.after(0, lambda msg=err_msg: self._ai_status_var.set(f'Error: {msg}'))

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

    # ── Manager integration ────────────────────────────────────────────────────

    def _ensure_manager(self) -> None:
        """Create or reconfigure the Manager for the current project/model."""
        root = self.graph["meta"]["root"] if self.graph else ""
        if (self._manager is None
                or getattr(self._manager, "project_root", None) != root
                or getattr(self._manager, "model", None) != self._ai_model):
            self._manager = Manager(
                project_root   = root,
                graph          = self.graph,
                model          = self._ai_model,
                on_text        = lambda chunk: self.after(0, lambda c=chunk: self._ai_append_content(c)),
                on_tool        = lambda name, args: self.after(0, lambda n=name, a=args: self._log_tool(n, a)),
                on_team_event  = lambda evt: self.after(0, lambda e=evt: self._log_team_event(e)),
                on_graph_changed = lambda: self.after(0, self._on_graph_changed),
                on_done        = lambda: self.after(0, self._on_manager_done),
                on_log         = lambda text, tag='dim': self.after(0,
                    lambda t=text, g=tag: self._teams_log_append(t, g)),
                on_tool_missing = lambda spec: self.after(0,
                    lambda s=spec: self._on_tool_missing(s)),
            )

    def _on_manager_done(self) -> None:
        """Called when Manager finishes a turn."""
        self._refresh_plan()
        if self._bake_running and self._bake_deadline > 0:
            if time.time() < self._bake_deadline:
                remaining = int((self._bake_deadline - time.time()) / 60)
                self._ai_append(f"\n[Bake: {remaining}m remaining]\n", "dim")
            else:
                self._bake_stop()

    def _on_graph_changed(self) -> None:
        """Called when Manager writes a file — re-parse and highlight changed nodes."""
        if not self.graph:
            return
        root = self.graph["meta"]["root"]
        old_ids = {n["id"] for n in (self.graph.get("nodes") or [])}
        def _after_parse():
            new_ids = {n["id"] for n in (self.graph.get("nodes") or [])}
            self._changed_nodes = (new_ids - old_ids) if (new_ids - old_ids) else new_ids
            self._redraw()
            self.after(3000, lambda: (self._changed_nodes.clear(), self._redraw()))
        self._load_project(root)
        self.after(150, _after_parse)
        self.after(400, self._sessions_refresh)

    def _log_tool(self, name: str, args: dict) -> None:
        """Show tool call in both AI chat and Teams Log."""
        arg_str = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
        ai_line = f"  → {name}({arg_str[:80]})\n"
        ts = __import__('time').strftime('%H:%M:%S')
        log_line = f"[{ts}] [Manager] → {name}({arg_str[:60]})\n"
        self._ai_append(ai_line, "tool")
        self._teams_log_append(log_line, "tool")

    def _log_team_event(self, evt) -> None:
        """Handle a team-delegation action (dict) or live TeamEvent."""
        if isinstance(evt, dict):
            # JSON run_team block emitted by Manager._check_for_team_action
            action_agents = evt.get("agents", [])
            task          = evt.get("task", "")
            role_list     = ", ".join(a.get("role", "?") for a in action_agents)
            self._ai_append(f"\n◉ Delegating to team: {role_list}\n", "tool")
            self._ai_append(f"  Task: {task[:120]}\n", "dim")
            if not task:
                return
            # Populate Teams canvas from Manager's agent list
            if action_agents:
                self._tw_nodes.clear()
                self._tw_edges.clear()
                prev_id = None
                for i, acfg in enumerate(action_agents):
                    nid = self._tw_new_node(acfg.get("role", "implementer"),
                                            x=80 + i * 260, y=80)
                    n = self._tw_node_by_id(nid)
                    if n:
                        n["model"] = acfg.get("model", self._ai_model)
                    if prev_id:
                        self._tw_edges.append({"id": f"te_{prev_id}_{nid}",
                                               "source": prev_id, "target": nid})
                    prev_id = nid
            # Write task to Plan tab
            w = getattr(self, "_plan_text", None)
            if w:
                try:
                    w.config(state="normal")
                    w.delete("1.0", "end")
                    w.insert("1.0", task)
                    w.config(state="disabled")
                except Exception:
                    pass
            # Switch to Teams view and run
            if self.canvas_mode != "teams":
                self._toggle_teams_mode()
            self.after(200, self._tw_run_workflow)
        else:
            # Live TeamEvent — log to Teams Log and AI chat summary
            tag = ("tool"  if evt.type in ("handoff", "start") else
                   "error" if evt.type == "error" else "dim")
            self._ai_append(f"  [{evt.agent}] {evt.message}\n", tag)
            self._teams_log_append(f"[{evt.agent}] {evt.message}\n", tag)

    # ── Bake ───────────────────────────────────────────────────────────────────

    def _bake_btn_click(self) -> None:
        """Toggle bake on/off from the button."""
        if self._bake_running:
            self._bake_stop()
            return
        # Get task from Plan tab
        task = ""
        w = getattr(self, '_plan_text', None)
        if w:
            try: task = w.get('1.0', 'end-1c').strip()
            except Exception: pass
        if not task or task == 'Describe the task for the AI team here...':
            self._select_bottom_tab('plan')
            self._ai_append('\nType your task in the Plan tab, then click Bake.\n', 'dim')
            return
        minutes = 10   # default bake time
        self._ensure_manager()
        self._manager.bake(task=task, minutes=minutes)
        self._bake_start(minutes)

    def _bake_start(self, minutes: int) -> None:
        """Start a bake session — open Teams Log and update UI."""
        self._bake_running = True
        self._bake_deadline = time.time() + minutes * 60
        self._ai_append(f"\n🔥 Bake started — {minutes} minute budget.\n", "tool")
        self._select_bottom_tab("teams")
        self._teams_log_append(
            f"{'─'*50}\n🔥 BAKE — {minutes}m budget\n{'─'*50}\n", "header")
        self._update_bake_btn(running=True, minutes=minutes)
        self._bake_tick()

    def _bake_stop(self) -> None:
        """Stop a running bake."""
        self._bake_running = False
        self._bake_deadline = 0.0
        if self._manager:
            self._manager.stop()
        self._update_bake_btn(running=False)
        self._ai_append("\n🔥 Bake complete.\n", "tool")

    def _bake_tick(self) -> None:
        """Update countdown label every 30s while bake runs."""
        if not self._bake_running or not self._bake_deadline:
            return
        remaining = max(0, self._bake_deadline - time.time())
        mins = int(remaining // 60)
        self._update_bake_btn(running=True, minutes=mins)
        if remaining > 0:
            self.after(30000, self._bake_tick)
        else:
            self._bake_stop()

    def _update_bake_btn(self, running: bool, minutes: int = 0) -> None:
        """Update the bake button appearance."""
        btn = getattr(self, "_bake_btn", None)
        if not btn:
            return
        from gui.app import P
        if running:
            btn.config(text=f"⏹ {minutes}m", bg=P["amber"], fg="#000",
                       highlightbackground=P["amber"])
        else:
            btn.config(text="🔥 Bake", bg=P["bg3"], fg=P["t2"],
                       highlightbackground=P["line2"])

    # ── New project wizard ─────────────────────────────────────────────────────

    def _on_tool_missing(self, spec) -> None:
        """Show tool-build approval dialog when Manager calls an unknown tool."""
        self._ai_append(
            f"\n⚙ Agent wants a tool that doesn't exist: '{spec.tool_name}'\n"
            f"  Intent: {spec.intent[:120]}\n", "tool")
        self._teams_log_append(
            f"[ToolMissing] {spec.tool_name}\n{spec.summary()}\n", "tool")

        win = tk.Toplevel(self)
        win.title(f"Build tool: {spec.tool_name}?")
        win.configure(bg=P["bg1"])
        win.geometry("500x420")
        win.resizable(True, True)
        win.transient(self)

        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")
        hdr = tk.Frame(win, bg=P["bg2"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="⚙ BUILD NEW TOOL?", bg=P["bg2"], fg=P["amber"],
                 font=self._mono_l, padx=14, pady=10).pack(anchor="w")
        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")

        body = tk.Frame(win, bg=P["bg0"])
        body.pack(fill="both", expand=True)
        sb = tk.Scrollbar(body); sb.pack(side="right", fill="y")
        txt = tk.Text(body, bg=P["bg0"], fg=P["t1"],
                      font=(self._mono_xs.actual()["family"], 9),
                      yscrollcommand=sb.set, bd=0, wrap="word",
                      state="disabled", padx=12, pady=8)
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)
        txt.tag_config("header", foreground=P["amber"],
                       font=(self._mono_xs.actual()["family"], 9, "bold"))
        txt.tag_config("dim",    foreground=P["t2"])
        txt.config(state="normal")
        txt.insert("end", f"Tool name: {spec.tool_name}\n", "header")
        txt.insert("end", f"Description: {spec.description}\n\n", "")
        txt.insert("end", "Why the agent needs it:\n", "dim")
        txt.insert("end", spec.intent[:400] + "\n\n", "")
        txt.insert("end", "What will be built:\n", "dim")
        txt.insert("end", spec.to_team_task()[:600] + "\n", "")
        txt.config(state="disabled")

        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")
        btn_row = tk.Frame(win, bg=P["bg1"])
        btn_row.pack(fill="x", padx=14, pady=10)

        def _approve():
            win.destroy()
            self._ai_append(
                f"\n✓ Approved. Starting tool-build team for '{spec.tool_name}'...\n",
                "tool")
            self._build_missing_tool(spec)

        def _reject():
            win.destroy()
            self._ai_append(
                f"\n✗ Rejected. Continuing without '{spec.tool_name}'.\n",
                "dim")
            if self._manager:
                self._manager.reject_tool_build()

        approve_btn = tk.Label(btn_row, text="✓ Build it",
                               bg=P["green2"], fg=P["green"],
                               font=self._mono_s, padx=12, pady=4,
                               cursor="hand2",
                               highlightbackground=P["green"], highlightthickness=1)
        approve_btn.pack(side="left")
        approve_btn.bind("<Button-1>", lambda _: _approve())

        reject_btn = tk.Label(btn_row, text="✗ Skip",
                              bg=P["bg3"], fg=P["t2"],
                              font=self._mono_xs, padx=8, pady=4,
                              cursor="hand2",
                              highlightbackground=P["line2"], highlightthickness=1)
        reject_btn.pack(side="right")
        reject_btn.bind("<Button-1>", lambda _: _reject())
        win.protocol("WM_DELETE_WINDOW", _reject)

    def _build_missing_tool(self, spec) -> None:
        """Run a 3-agent tool-building team, then approve into Manager."""

        root = self.project_root or (
            self.graph["meta"]["root"] if self.graph else "")
        if not root:
            self._ai_append("No project loaded — cannot build tool.\n", "error")
            if self._manager:
                self._manager.reject_tool_build()
            return

        self._select_bottom_tab("teams")
        self._teams_log_append(
            f"{'─'*50}\n⚙ Building tool: {spec.tool_name}\n{'─'*50}\n",
            "header")

        def _run():
            def _evt(e):
                self.after(0, lambda ev=e: self._tw_on_event(ev))
            built = build_tool_with_team(
                spec         = spec,
                project_root = root,
                graph        = self.graph,
                model        = self._ai_model,
                on_event     = _evt,
            )
            self.after(0, lambda p=built: self._on_tool_built(spec, p))

        threading.Thread(target=_run, daemon=True).start()

    def _on_tool_built(self, spec, built_path) -> None:
        """Called when the tool-building team finishes."""
        if built_path:
            self._ai_append(
                f"\n✓ Tool '{spec.tool_name}' built at {built_path}\n"
                f"  Resuming original task...\n", "tool")
            self._teams_log_append(
                f"⚙ Tool registered: {spec.tool_name}\n", "tool")
        else:
            self._ai_append(
                f"\n✗ Tool '{spec.tool_name}' build failed.\n"
                f"  Continuing without it.\n", "error")
        if self._manager:
            self._manager.approve_tool_build(built_path)

    def _new_project_dialog(self) -> None:
        """Show dialog to create a new scaffolded project."""
        win = tk.Toplevel(self)
        win.title("New Project")
        win.configure(bg=P["bg1"])
        win.geometry("420x280")
        win.resizable(False, False)
        win.transient(self)

        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")
        tk.Label(win, text="NEW PROJECT", bg=P["bg1"], fg=P["t0"],
                 font=self._mono_l, padx=14, pady=10).pack(anchor="w")
        tk.Frame(win, bg=P["line"], height=1).pack(fill="x")

        body = tk.Frame(win, bg=P["bg1"])
        body.pack(fill="both", expand=True, padx=16, pady=10)

        def _row(label, var, placeholder=""):
            r = tk.Frame(body, bg=P["bg1"])
            r.pack(fill="x", pady=4)
            tk.Label(r, text=label, bg=P["bg1"], fg=P["t2"],
                     font=self._mono_xs, width=12, anchor="w").pack(side="left")
            e = tk.Entry(r, textvariable=var, bg=P["bg3"], fg=P["t0"], bd=0,
                         insertbackground=P["green"], font=self._mono_s, width=28)
            e.pack(side="left", ipady=3)
            return e

        name_var  = tk.StringVar(value="my-project")
        desc_var  = tk.StringVar(value="")
        dir_var   = tk.StringVar(value=os.path.expanduser("~/DevOps"))

        _row("Name",        name_var)
        _row("Description", desc_var)

        dir_row = tk.Frame(body, bg=P["bg1"])
        dir_row.pack(fill="x", pady=4)
        tk.Label(dir_row, text="Parent dir", bg=P["bg1"], fg=P["t2"],
                 font=self._mono_xs, width=12, anchor="w").pack(side="left")
        tk.Entry(dir_row, textvariable=dir_var, bg=P["bg3"], fg=P["t0"], bd=0,
                 insertbackground=P["green"], font=self._mono_s, width=22).pack(side="left", ipady=3)
        browse = tk.Label(dir_row, text="…", bg=P["bg3"], fg=P["t2"],
                          font=self._mono_xs, padx=6, cursor="hand2",
                          highlightbackground=P["line2"], highlightthickness=1)
        browse.pack(side="left", padx=(4, 0))
        browse.bind("<Button-1>", lambda _: dir_var.set(
            filedialog.askdirectory(title="Parent directory") or dir_var.get()))

        tk.Frame(body, bg=P["line"], height=1).pack(fill="x", pady=8)
        btn_row = tk.Frame(body, bg=P["bg1"])
        btn_row.pack(fill="x")

        def _create():
            name = name_var.get().strip()
            if not name:
                return
            try:
                root = scaffold_new_project(
                    parent_dir  = dir_var.get(),
                    name        = name,
                    description = desc_var.get().strip(),
                )
                win.destroy()
                self._load_project(root)
                self._ai_append(f"\n✓ Project created: {root}\n", "tool")
            except Exception as e:
                self._ai_append(f"\nError creating project: {e}\n", "error")

        create_btn = tk.Label(btn_row, text="Create & Open", bg=P["green2"], fg=P["green"],
                              font=self._mono_s, padx=12, pady=4, cursor="hand2",
                              highlightbackground=P["green"], highlightthickness=1)
        create_btn.pack(side="left")
        create_btn.bind("<Button-1>", lambda _: _create())

        cancel_btn = tk.Label(btn_row, text="Cancel", bg=P["bg3"], fg=P["t2"],
                              font=self._mono_xs, padx=8, pady=4, cursor="hand2",
                              highlightbackground=P["line2"], highlightthickness=1)
        cancel_btn.pack(side="right")
        cancel_btn.bind("<Button-1>", lambda _: win.destroy())
        win.bind("<Return>", lambda _: _create())
        win.bind("<Escape>", lambda _: win.destroy())

    def _build_teams_log_panel(self, parent) -> None:
        """Teams Log: live event stream on right, session browser on left."""
        mono = self._mono_xs.actual()["family"]
        # Header
        hdr = tk.Frame(parent, bg=P["bg2"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="Teams Log", bg=P["bg2"], fg=P["t2"],
                 font=self._mono_xs, padx=10, pady=5).pack(side="left")
        refresh_btn = tk.Label(hdr, text="Sessions", bg=P["bg3"], fg=P["t2"],
                               font=self._mono_xs, padx=6, pady=3, cursor="hand2",
                               highlightbackground=P["line2"], highlightthickness=1)
        refresh_btn.pack(side="right", padx=(4, 6), pady=4)
        refresh_btn.bind("<Button-1>", lambda _: self._sessions_refresh())
        clr = tk.Label(hdr, text="Clear", bg=P["bg3"], fg=P["t2"],
                       font=self._mono_xs, padx=6, pady=3, cursor="hand2",
                       highlightbackground=P["line2"], highlightthickness=1)
        clr.pack(side="right", padx=(0, 2), pady=4)
        clr.bind("<Button-1>", lambda _: self._teams_log_clear())
        tk.Frame(parent, bg=P["line"], height=1).pack(fill="x")
        # Split pane
        pw = tk.PanedWindow(parent, orient="horizontal", bg=P["line2"],
                            bd=0, sashwidth=3, sashpad=0)
        pw.pack(fill="both", expand=True)
        # Left: session list
        sess_f = tk.Frame(pw, bg=P["bg1"])
        pw.add(sess_f, width=155, stretch="never")
        tk.Label(sess_f, text="SESSIONS", bg=P["bg1"], fg=P["t3"],
                 font=(mono, 7), padx=8, pady=4).pack(anchor="w")
        sess_sb = tk.Scrollbar(sess_f)
        sess_sb.pack(side="right", fill="y")
        self._sess_list = tk.Listbox(
            sess_f, bg=P["bg1"], fg=P["t2"], font=(mono, 8), bd=0,
            highlightthickness=0, selectbackground=P["bg3"],
            selectforeground=P["t0"], activestyle="none",
            yscrollcommand=sess_sb.set)
        self._sess_list.pack(fill="both", expand=True)
        sess_sb.config(command=self._sess_list.yview)
        self._sess_list.bind("<ButtonRelease-1>",
                             lambda _: self._sessions_open_selected())
        # Right: live log
        log_f = tk.Frame(pw, bg=P["bg0"])
        pw.add(log_f, stretch="always")
        log_sb = tk.Scrollbar(log_f)
        log_sb.pack(side="right", fill="y")
        self._teams_log = tk.Text(
            log_f, bg=P["bg0"], fg=P["t2"], font=(mono, 9),
            yscrollcommand=log_sb.set, bd=0, wrap="word",
            state="disabled", padx=10, pady=8)
        self._teams_log.pack(fill="both", expand=True)
        log_sb.config(command=self._teams_log.yview)
        self._teams_log.tag_config("tool",   foreground=P["amber"])
        self._teams_log.tag_config("error",  foreground=P["red"])
        self._teams_log.tag_config("dim",    foreground=P["t3"])
        self._teams_log.tag_config("header", foreground=P["t0"],
                                   font=(mono, 9, "bold"))
        self.after(600, self._sessions_refresh)

    def _sessions_refresh(self) -> None:
        """Reload session list from .side/session/ for the current project."""
        lb = getattr(self, "_sess_list", None)
        if not lb:
            return
        root = self.project_root or (
            self.graph["meta"]["root"] if self.graph else "")
        if not root:
            return
        sessions = list_sessions(root)
        self._sess_data = sessions
        try:
            lb.delete(0, "end")
            for s in sessions:
                age = time.time() - s.get("modified", 0)
                if age < 3600:
                    age_s = str(int(age // 60)) + "m"
                elif age < 86400:
                    age_s = str(int(age // 3600)) + "h"
                else:
                    age_s = str(int(age // 86400)) + "d"
                lb.insert("end", s["id"][:7] + "  " + age_s + " ago")
        except Exception:
            pass

    def _sessions_open_selected(self) -> None:
        """Show selected past session in the log panel."""
        lb = getattr(self, "_sess_list", None)
        data = getattr(self, "_sess_data", [])
        if not lb:
            return
        sel = lb.curselection()
        if not sel or sel[0] >= len(data):
            return
        session = data[sel[0]]
        sess_dir = session["session_dir"]
        self._teams_log_clear()
        sep = "-" * 50
        self._teams_log_append(
            sep + "\nSession: " + session["id"] + "\n" + sep + "\n", "header")
        for fname in [
            "TASK.md", "plan/architecture.md", "plan/task.md",
            "review/findings.md", "review/verdict.md",
            "test/results.md", "test/verdict.md",
            "docs/changelog_entry.md",
        ]:
            fpath = os.path.join(sess_dir, fname)
            if not os.path.isfile(fpath):
                continue
            self._teams_log_append("\n-- " + fname + " --\n", "header")
            try:
                content = open(fpath, encoding="utf-8",
                               errors="replace").read()
                preview = content[:1600]
                if len(content) > 1600:
                    preview += "\n... [truncated]"
                self._teams_log_append(preview, "")
            except Exception:
                pass

    def _teams_log_append(self, text: str, tag: str = "") -> None:
        """Append text to the Teams Log. Safe to call from any thread."""
        def _do():
            w = getattr(self, "_teams_log", None)
            if not w:
                return
            try:
                w.config(state="normal")
                w.insert("end", text, tag)
                w.see("end")
                w.config(state="disabled")
            except Exception:
                pass
        self.after(0, _do)

    def _teams_log_clear(self) -> None:
        """Clear the Teams Log widget."""
        w = getattr(self, "_teams_log", None)
        if not w:
            return
        try:
            w.config(state="normal")
            w.delete("1.0", "end")
            w.config(state="disabled")
        except Exception:
            pass

    def _build_plan_panel(self, parent):
        """Build the plan panel — task description for Teams workflow."""
        tb = tk.Frame(parent, bg=P["bg2"])
        tb.pack(fill="x")
        tk.Label(tb, text="Task / Plan", bg=P["bg2"], fg=P["t2"],
                 font=self._mono_xs, padx=10, pady=5).pack(side="left")
        run_btn = tk.Label(tb, text="▶ Run Workflow", bg=P["green2"], fg=P["green"],
                           font=self._mono_xs, padx=8, pady=3, cursor="hand2",
                           highlightbackground=P["green"], highlightthickness=1)
        run_btn.pack(side="right", padx=6, pady=4)
        run_btn.bind("<Button-1>", lambda _: self._tw_run_workflow())
        tk.Frame(parent, bg=P["line"], height=1).pack(fill="x")
        self._plan_text = tk.Text(parent, bg=P["bg1"], fg=P["t1"], font=self._mono_s,
                                 padx=15, pady=15, borderwidth=0, highlightthickness=0)
        self._plan_text.config(state='normal')
        self._plan_text.insert('1.0', 'Describe the task for the AI team here...')
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
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._play_out.insert("end", f"\n-- [{ts}] Running --\n")
        
        # Save to temp file and run
        tmp = os.path.join(self.project_root, ".side", "playground_scratch.py")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w") as f: f.write(code)
        
        try:
            res = subprocess.run([sys.executable, tmp], capture_output=True, text=True, timeout=10)
            if res.stdout: self._play_out.insert("end", res.stdout)
            if res.stderr: self._play_out.insert("end", res.stderr, "warn")
        except Exception as e:
            self._play_out.insert("end", f"Error: {e}\n", "warn")
        self._play_out.see("end")


