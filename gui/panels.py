"""
gui/panels.py
=============
Bottom-panel tab content builders, extracted from app.py.

Each function takes the tab's parent Frame and an AppShell reference
(the main SIDE_App instance) and builds the tab's content in place.

Tabs
----
  build_projects_tab(parent, app)  — project list + version explorer + git
  build_ai_tab(parent, app)        — AI chat with markdown + streaming
  build_terminal_tab(parent, app)  — embedded terminal with history
  build_proc_tab(parent, app)      — process manager
  build_build_tab(parent, app)     — clean / build / package
  build_log_tab(parent, app)       — log viewer

The app shell exposes these attributes that panels read/write:
  app.graph        — current graph dict
  app.processes    — {proc_id: ManagedProcess}
  app._session     — SessionState
  app._ai_*        — AI state vars (set by build_ai_tab)
  app._proc_*      — proc panel widget refs (set by build_proc_tab)
  app._term_*      — terminal widget refs (set by build_terminal_tab)
  app._build_*     — build panel widget refs (set by build_build_tab)
  app._log_text    — log text widget (set by build_log_tab)
"""

from __future__ import annotations
import os
import re
import subprocess
import threading
from typing import TYPE_CHECKING
from gui.markdown import ai_append_markdown, _insert_inline


if TYPE_CHECKING:
    pass

# tk/ttk imported locally inside each function so this module can be
# imported in test environments where no display is available.


# ═══════════════════════════════════════════════════════════════════════════════
# PROJECTS tab
# ═══════════════════════════════════════════════════════════════════════════════

def build_projects_tab(parent: tk.Frame, app) -> None:
    """Project list | Version explorer | Git status."""
    import tkinter as tk
    import tkinter.ttk as ttk
    from gui.app import P, fmt_size
    from gui.app import P, fmt_size

    # ── Left: project list ────────────────────────────────────────────────────
    left = tk.Frame(parent, bg=P["bg1"], width=220)
    left.pack(side="left", fill="y")
    left.pack_propagate(False)
    tk.Frame(parent, bg=P["line"], width=1).pack(side="left", fill="y")

    lh = tk.Frame(left, bg=P["bg1"])
    lh.pack(fill="x", padx=10, pady=(8, 4))
    tk.Label(lh, text="PROJECTS", bg=P["bg1"], fg=P["t3"],
             font=app._mono_xs).pack(side="left")
    ob = tk.Label(lh, text="+ Open", bg=P["bg3"], fg=P["green"],
                  font=app._mono_xs, padx=6, pady=2, cursor="hand2",
                  highlightbackground=P["green2"], highlightthickness=1)
    ob.pack(side="right")
    ob.bind("<Button-1>", lambda _: app._open_project_dialog())

    app._bp_proj_list = tk.Frame(left, bg=P["bg1"])
    app._bp_proj_list.pack(fill="both", expand=True, padx=4)

    # ── Centre: version explorer ──────────────────────────────────────────────
    mid = tk.Frame(parent, bg=P["bg1"])
    mid.pack(side="left", fill="both", expand=True)

    vh = tk.Frame(mid, bg=P["bg1"])
    vh.pack(fill="x", padx=10, pady=(8, 4))
    tk.Label(vh, text="VERSIONS", bg=P["bg1"], fg=P["t3"],
             font=app._mono_xs).pack(side="left")
    archive_btn = tk.Label(vh, text="📦 Archive", bg=P["bg3"], fg=P["t2"],
                            font=app._mono_xs, padx=6, pady=2, cursor="hand2",
                            highlightbackground=P["line2"], highlightthickness=1)
    archive_btn.pack(side="right")
    archive_btn.bind("<Button-1>", lambda _: app._archive_version())

    app._bp_ver_list = tk.Frame(mid, bg=P["bg1"])
    app._bp_ver_list.pack(fill="both", expand=True, padx=4)

    # ── Right: git status ─────────────────────────────────────────────────────
    tk.Frame(parent, bg=P["line"], width=1).pack(side="left", fill="y")
    right = tk.Frame(parent, bg=P["bg1"], width=200)
    right.pack(side="left", fill="y")
    right.pack_propagate(False)

    gh = tk.Frame(right, bg=P["bg1"])
    gh.pack(fill="x", padx=8, pady=(8, 4))
    tk.Label(gh, text="GIT", bg=P["bg1"], fg=P["t3"],
             font=app._mono_xs).pack(side="left")
    git_refresh = tk.Label(gh, text="↻", bg=P["bg1"], fg=P["t2"],
                            font=app._mono_xs, cursor="hand2")
    git_refresh.pack(side="right")
    git_refresh.bind("<Button-1>", lambda _: refresh_git(app))

    app._bp_git_text = tk.Text(right, bg=P["bg0"], fg=P["t1"],
                                font=(app._mono_xs.actual()["family"], 9),
                                bd=0, state="disabled", wrap="char", padx=8)
    app._bp_git_text.pack(fill="both", expand=True, pady=4)
    app._bp_git_text.tag_config("add", foreground=P["green"])
    app._bp_git_text.tag_config("del", foreground=P["red"])
    app._bp_git_text.tag_config("mod", foreground=P["amber"])
    app._bp_git_text.tag_config("dim", foreground=P["t2"])

    # ── Bottom: run scripts panel ────────────────────────────────────────────
    # Build a thin RUN strip below the main content
    tk.Frame(parent, bg=P["line"], height=1).pack(fill="x", side="bottom")
    run_strip = tk.Frame(parent, bg=P["bg1"])
    run_strip.pack(side="bottom", fill="x")

    # Temporarily make _build_run_panel and _build_version_panel use run_strip
    # by setting app._sidebar to run_strip for the duration of the call
    old_sidebar = getattr(app, "_sidebar", None)
    app._sidebar = run_strip
    try:
        app._build_run_panel()
        app._build_version_panel()
    except Exception as e:
        import traceback; traceback.print_exc()
    finally:
        app._sidebar = old_sidebar

    refresh_project_list(app)
    refresh_git(app)


def refresh_project_list(app) -> None:
    """Rebuild the project list in the PROJECTS tab."""
    import tkinter as tk
    import tkinter.ttk as ttk
    from gui.app import P, fmt_size
    from gui.app import P
    box = getattr(app, "_bp_proj_list", None)
    if not box:
        return
    for w in box.winfo_children():
        w.destroy()
    for p in app.projects[:25]:
        root_now = app.graph["meta"]["root"] if app.graph else None
        is_cur = root_now and os.path.normcase(root_now) == os.path.normcase(p["path"])
        row = tk.Frame(box, bg=P["bg3"] if is_cur else P["bg1"], cursor="hand2")
        row.pack(fill="x", pady=1)
        tk.Frame(row, bg=P["green"] if is_cur else P["t3"],
                 width=4).pack(side="left", fill="y")
        tk.Label(row, text=p["name"][:24], bg=row["bg"],
                 fg=P["t0"] if is_cur else P["t2"],
                 font=app._mono_s, anchor="w", padx=8, pady=4).pack(
                 side="left", fill="x", expand=True)
        d = tk.Label(row, text="✕", bg=row["bg"], fg=P["t3"],
                     font=app._mono_xs, padx=6, cursor="hand2")
        d.pack(side="right")
        d.bind("<Button-1>", lambda _, pp=p["path"]: app._remove_project(pp))
        row.bind("<Button-1>", lambda _, pp=p["path"]: app._load_project(pp))
        for c in row.winfo_children():
            if c is not d:
                c.bind("<Button-1>", lambda _, pp=p["path"]: app._load_project(pp))
    refresh_version_list(app)


def refresh_version_list(app) -> None:
    import tkinter as tk
    import tkinter.ttk as ttk
    from gui.app import P, fmt_size
    from gui.app import P, fmt_size
    box = getattr(app, "_bp_ver_list", None)
    if not box:
        return
    for w in box.winfo_children():
        w.destroy()
    if not app.graph:
        tk.Label(box, text="no project", bg=P["bg1"], fg=P["t3"],
                 font=app._mono_xs).pack(anchor="w")
        return
    try:
        from version.version_manager import list_versions
        versions = list_versions(app.graph["meta"]["root"])
    except Exception:
        versions = []
    for v in versions[:20]:
        row = tk.Frame(box, bg=P["bg1"])
        row.pack(fill="x", pady=1)
        icon = "🗜" if v["type"] == "tarball" else "📁"
        tk.Label(row, text=f"{icon} {v['name'][:28]}", bg=P["bg1"],
                 fg=P["t2"], font=app._mono_xs).pack(side="left", fill="x", expand=True)
        tk.Label(row, text=fmt_size(v["size"]), bg=P["bg1"],
                 fg=P["t3"], font=app._mono_xs).pack(side="right", padx=4)


def refresh_git(app) -> None:
    import tkinter as tk
    import tkinter.ttk as ttk
    from gui.app import P, fmt_size
    from gui.app import P
    w = getattr(app, "_bp_git_text", None)
    if not w:
        return
    try:
        w.config(state="normal")
        w.delete("1.0", "end")
    except Exception:
        return
    if not app.graph:
        w.insert("end", "no project\n", "dim")
        w.config(state="disabled")
        return
    root = app.graph["meta"]["root"]
    try:
        rb = subprocess.run("git branch --show-current", shell=True, cwd=root,
                            capture_output=True, text=True, timeout=3)
        if rb.returncode == 0 and rb.stdout.strip():
            w.insert("end", f"branch: {rb.stdout.strip()}\n\n", "dim")
        r = subprocess.run("git status --short", shell=True, cwd=root,
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            w.insert("end", "not a git repo\n", "dim")
        elif not r.stdout.strip():
            w.insert("end", "working tree clean\n", "dim")
        else:
            for line in r.stdout.splitlines()[:30]:
                tag = ("add" if line[:2].strip() in ("A", "??") else
                       "del" if line[:2].strip() == "D" else "mod")
                w.insert("end", line + "\n", tag)
    except Exception as e:
        w.insert("end", f"error: {e}\n", "mod")
    w.config(state="disabled")


# ═══════════════════════════════════════════════════════════════════════════════
# AI tab
# ═══════════════════════════════════════════════════════════════════════════════

def build_ai_tab(parent: tk.Frame, app) -> None:
    """Streaming AI chat with markdown rendering and tool-call display."""
    import tkinter as tk
    import tkinter.ttk as ttk
    from gui.app import P, fmt_size
    from gui.app import P

    # ── Control bar ───────────────────────────────────────────────────────────
    ctrl = tk.Frame(parent, bg=P["bg1"])
    ctrl.pack(fill="x")
    tk.Frame(ctrl, bg=P["line"], height=1).pack(fill="x")
    ci = tk.Frame(ctrl, bg=P["bg1"])
    ci.pack(fill="x", padx=10, pady=4)

    app._ai_status_var = tk.StringVar(value="checking Ollama…")
    tk.Label(ci, textvariable=app._ai_status_var, bg=P["bg1"],
             fg=P["t2"], font=app._mono_xs).pack(side="left")

    app._ai_model_var = tk.StringVar(value=getattr(app, "_ai_model", "llama3.2"))
    model_combo = ttk.Combobox(ci, textvariable=app._ai_model_var,
                                width=16, font=app._mono_xs, state="readonly")
    model_combo.pack(side="left", padx=8)
    model_combo.bind("<<ComboboxSelected>>",
                     lambda _: setattr(app, "_ai_model", app._ai_model_var.get()))

    app._ai_thinking_var = tk.BooleanVar(value=False)
    tk.Checkbutton(ci, text="thinking", variable=app._ai_thinking_var,
                   bg=P["bg1"], fg=P["t2"], selectcolor=P["bg3"],
                   activebackground=P["bg1"], font=app._mono_xs).pack(side="left", padx=8)

    clear_btn = tk.Label(ci, text="Clear", bg=P["bg3"], fg=P["t2"],
                          font=app._mono_xs, padx=6, pady=2, cursor="hand2",
                          highlightbackground=P["line2"], highlightthickness=1)
    clear_btn.pack(side="right")
    clear_btn.bind("<Button-1>", lambda _: app._ai_clear())
    tk.Frame(ctrl, bg=P["line"], height=1).pack(fill="x")

    # ── Conversation ──────────────────────────────────────────────────────────
    conv_f = tk.Frame(parent, bg=P["bg0"])
    conv_f.pack(fill="both", expand=True)
    csb = tk.Scrollbar(conv_f)
    csb.pack(side="right", fill="y")
    app._ai_conv = tk.Text(
        conv_f, bg=P["bg0"], fg=P["t1"],
        font=(app._mono_xs.actual()["family"], 10),
        yscrollcommand=csb.set, bd=0, wrap="word",
        state="disabled", padx=14, pady=8,
    )
    app._ai_conv.pack(fill="both", expand=True)
    csb.config(command=app._ai_conv.yview)

    mono = app._mono_xs.actual()["family"]
    app._ai_conv.tag_config("user",     foreground=P["cyan"],  font=(mono, 10, "bold"))
    app._ai_conv.tag_config("ai",       foreground=P["t0"],    font=(mono, 10))
    app._ai_conv.tag_config("tool_hdr", foreground=P["amber"], font=(mono, 9))
    app._ai_conv.tag_config("tool_res", foreground=P["t2"],    font=(mono, 9))
    app._ai_conv.tag_config("error",    foreground=P["red"])
    app._ai_conv.tag_config("dim",      foreground=P["t2"],    font=(mono, 9))
    app._ai_conv.tag_config("code",     foreground=P["green"],
                             background=P["bg2"], font=(mono, 9),
                             lmargin1=20, lmargin2=20)
    app._ai_conv.tag_config("h1",       foreground=P["t0"],    font=(mono, 12, "bold"))
    app._ai_conv.tag_config("h2",       foreground=P["t0"],    font=(mono, 10, "bold"))
    app._ai_conv.tag_config("h3",       foreground=P["t1"],    font=(mono, 10, "bold"))
    app._ai_conv.tag_config("bullet",   foreground=P["cyan"],  font=(mono, 10))
    app._ai_conv.tag_config("strong",   foreground=P["t0"],    font=(mono, 10, "bold"))
    app._ai_conv.tag_config("em",       foreground=P["t1"],    font=(mono, 10, "italic"))
    app._ai_conv.tag_config("thinking", foreground=P["t3"],    font=(mono, 9, "italic"))

    # ── Input ─────────────────────────────────────────────────────────────────
    tk.Frame(parent, bg=P["line"], height=1).pack(fill="x")
    inp_f = tk.Frame(parent, bg=P["bg2"])
    inp_f.pack(fill="x", padx=10, pady=6)
    app._ai_input_var = tk.StringVar()
    app._ai_entry = None  # set after creation
    inp = tk.Entry(inp_f, textvariable=app._ai_input_var,
                   bg=P["bg3"], fg=P["t0"], insertbackground=P["green"],
                   bd=0, font=(mono, 11), takefocus=True)
    inp.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 8))
    app._ai_entry = inp
    inp.bind("<Return>",    lambda _: app._ai_send())
    inp.bind("<Button-1>",  lambda _: inp.focus_force())
    # focus_set() deferred — set when tab becomes visible
    send_btn = tk.Label(inp_f, text="Send", bg=P["green2"], fg=P["green"],
                         font=app._mono_s, padx=10, pady=4, cursor="hand2",
                         highlightbackground=P["green"], highlightthickness=1)
    send_btn.pack(side="left")
    send_btn.bind("<Button-1>", lambda _: app._ai_send())

    threading.Thread(target=lambda: _check_ollama(app, model_combo),
                     daemon=True).start()
    _load_ai_history(app)


def _check_ollama(app, model_combo) -> None:
    from gui.app import P
    try:
        from ai.client import OllamaClient
        client = OllamaClient()
        if client.is_available():
            models = client.list_models()
            app._ai_available = True
            def _ok():
                app._ai_status_var.set(f"{len(models)} model(s)")
                model_combo.config(values=models)
                if models:
                    model_combo.set(models[0])
                    app._ai_model = models[0]
                if hasattr(app, "_ai_btn"):
                    app._ai_btn.config(fg=P["green"])
            app.after(0, _ok)
        else:
            def _no():
                app._ai_status_var.set("Ollama not running  (ollama serve)")
                if hasattr(app, "_ai_btn"):
                    app._ai_btn.config(fg=P["red"])
            app.after(0, _no)
    except Exception as e:
        app.after(0, lambda: app._ai_status_var.set(f"Error: {e}"))


def _load_ai_history(app) -> None:
    """Restore conversation from session state."""
    from ai.client import ChatMessage as CM
    root = app.graph["meta"]["root"] if app.graph else ""
    saved = app._session.get_ai_history(root)
    if not saved:
        ai_append(app, "Ask anything about the project. I can read files, "
                       "search definitions, run tests, check metrics, and use git.\n\n", "dim")
        return
    app._ai_messages = [CM(role=m["role"], content=m["content"]) for m in saved]
    for m in saved[-60:]:
        if m["role"] == "user":
            ai_append(app, f"\nYou: {m['content']}\n", "user")
        elif m["role"] == "assistant":
            ai_append_markdown(app, m["content"])
            ai_append(app, "\n", "")


def ai_append(app, text: str, tag: str = "") -> None:
    """Append plain text to the AI conversation widget."""
    w = getattr(app, "_ai_conv", None)
    if not w:
        return
    try:
        if not w.winfo_exists():
            return
        w.config(state="normal")
        w.insert("end", text, tag)
        w.see("end")
        w.config(state="disabled")
    except Exception:
        pass






# ═══════════════════════════════════════════════════════════════════════════════
# TERMINAL tab
# ═══════════════════════════════════════════════════════════════════════════════

def build_terminal_tab(parent: tk.Frame, app) -> None:
    import tkinter as tk
    import tkinter.ttk as ttk
    from gui.app import P, fmt_size
    from gui.app import P
    mono = app._mono_xs.actual()["family"]

    out_f = tk.Frame(parent, bg=P["bg0"])
    out_f.pack(fill="both", expand=True)
    tsb = tk.Scrollbar(out_f)
    tsb.pack(side="right", fill="y")
    app._term_out = tk.Text(
        out_f, bg=P["bg0"], fg=P["t1"],
        font=(mono, 10), yscrollcommand=tsb.set,
        bd=0, state="disabled", wrap="char", padx=10,
    )
    app._term_out.pack(fill="both", expand=True)
    tsb.config(command=app._term_out.yview)
    app._term_out.tag_config("err",    foreground=P["red"])
    app._term_out.tag_config("prompt", foreground=P["green"])
    app._term_out.tag_config("dim",    foreground=P["t2"])

    tk.Frame(parent, bg=P["line"], height=1).pack(fill="x")
    inp_f = tk.Frame(parent, bg=P["bg2"])
    inp_f.pack(fill="x", padx=8, pady=6)
    tk.Label(inp_f, text="$", bg=P["bg2"], fg=P["green"],
             font=(mono, 11)).pack(side="left", padx=(4, 6))

    app._term_var = tk.StringVar()
    app._term_hist_idx = -1
    app._term_entry = tk.Entry(inp_f, textvariable=app._term_var,
                                bg=P["bg3"], fg=P["t0"],
                                insertbackground=P["green"], bd=0,
                                font=(mono, 11), takefocus=True)
    app._term_entry.pack(side="left", fill="x", expand=True, ipady=4)
    app._term_entry.bind("<Return>",   lambda _: app._term_run())
    app._term_entry.bind("<Up>",       lambda _: app._term_hist_prev())
    app._term_entry.bind("<Down>",     lambda _: app._term_hist_next())
    app._term_entry.bind("<Button-1>", lambda _: app._term_entry.focus_force())

    root = app.graph["meta"]["root"] if app.graph else os.getcwd()
    _term_append(app, f"  {root}\n", "dim")


def _term_append(app, text: str, tag: str = "") -> None:
    w = getattr(app, "_term_out", None)
    if not w:
        return
    try:
        w.config(state="normal")
        w.insert("end", text, tag)
        w.see("end")
        w.config(state="disabled")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# PROC tab
# ═══════════════════════════════════════════════════════════════════════════════

def build_proc_tab(parent: tk.Frame, app) -> None:
    import tkinter as tk
    import tkinter.ttk as ttk
    from gui.app import P, fmt_size
    from gui.app import P

    top_bar = tk.Frame(parent, bg=P["bg1"])
    top_bar.pack(fill="x")
    hi = tk.Frame(top_bar, bg=P["bg1"])
    hi.pack(fill="x", padx=10, pady=6)
    app._proc_count_var = tk.StringVar(value="0")
    tk.Label(hi, textvariable=app._proc_count_var, bg=P["bg1"],
             fg=P["t2"], font=app._mono_xs).pack(side="left", padx=(0, 8))
    cs = tk.Label(hi, text="Clear stopped", bg=P["bg3"], fg=P["t2"],
                  font=app._mono_xs, padx=6, pady=2, cursor="hand2",
                  highlightbackground=P["line2"], highlightthickness=1)
    cs.pack(side="right")
    cs.bind("<Button-1>", lambda _: app._clear_stopped_procs())
    tk.Frame(top_bar, bg=P["line"], height=1).pack(fill="x")

    list_outer = tk.Frame(parent, bg=P["bg1"])
    list_outer.pack(fill="both", expand=True)
    scroll = tk.Scrollbar(list_outer)
    scroll.pack(side="right", fill="y")
    app._proc_list_canvas = tk.Canvas(list_outer, bg=P["bg1"],
                                       yscrollcommand=scroll.set,
                                       highlightthickness=0)
    app._proc_list_canvas.pack(fill="both", expand=True)
    scroll.config(command=app._proc_list_canvas.yview)
    app._proc_list_inner = tk.Frame(app._proc_list_canvas, bg=P["bg1"])
    app._proc_list_canvas.create_window((0, 0), window=app._proc_list_inner,
                                         anchor="nw")
    app._proc_list_canvas.bind("<Configure>",
        lambda e: app._proc_list_canvas.itemconfig(1, width=e.width))
    app._proc_list_inner.bind("<Configure>",
        lambda e: app._proc_list_canvas.configure(
            scrollregion=app._proc_list_canvas.bbox("all")))

    tk.Frame(parent, bg=P["line"], height=1).pack(fill="x")
    cmd_row = tk.Frame(parent, bg=P["bg2"])
    cmd_row.pack(fill="x", padx=10, pady=6)
    app._proc_cmd_var = tk.StringVar()
    cmd_e = tk.Entry(cmd_row, textvariable=app._proc_cmd_var,
                     bg=P["bg3"], fg=P["t1"], insertbackground=P["green"],
                     bd=0, font=app._mono_s, width=30)
    cmd_e.pack(side="left", fill="x", expand=True, ipady=3, padx=(0, 6))
    cmd_e.insert(0, "command…")
    cmd_e.bind("<FocusIn>", lambda e: (
        cmd_e.delete(0, "end") if cmd_e.get() == "command…" else None))
    cmd_e.bind("<Return>", lambda _: app._start_process())
    app._proc_cwd_var = tk.StringVar(
        value=app.graph["meta"]["root"] if app.graph else "")
    cwd_e = tk.Entry(cmd_row, textvariable=app._proc_cwd_var,
                     bg=P["bg3"], fg=P["t2"], insertbackground=P["green"],
                     bd=0, font=app._mono_xs, width=14)
    cwd_e.pack(side="left", ipady=3, padx=(0, 6))
    run_btn = tk.Label(cmd_row, text="Run", bg=P["green2"], fg=P["green"],
                        font=app._mono_s, padx=8, pady=3, cursor="hand2",
                        highlightbackground=P["green"], highlightthickness=1)
    run_btn.pack(side="left")
    run_btn.bind("<Button-1>", lambda _: app._start_process())
    app._render_proc_list()


# ═══════════════════════════════════════════════════════════════════════════════
# BUILD tab
# ═══════════════════════════════════════════════════════════════════════════════

def build_build_tab(parent: tk.Frame, app) -> None:
    import tkinter as tk
    import tkinter.ttk as ttk
    from gui.app import P, fmt_size
    from gui.app import P

    opts_f = tk.Frame(parent, bg=P["bg1"])
    opts_f.pack(fill="x", padx=14, pady=6)

    app._build_kind_var   = tk.StringVar(value="tarball")
    app._build_plat_var   = tk.StringVar(value="auto")
    app._build_bump_var   = tk.StringVar(value="none")
    app._build_minify_var = tk.BooleanVar(value=True)
    app._build_clean_var  = tk.BooleanVar(value=True)
    app._build_tests_var  = tk.BooleanVar(value=False)

    def _row(label, widget_fn):
        r = tk.Frame(opts_f, bg=P["bg1"])
        r.pack(fill="x", pady=2)
        tk.Label(r, text=label, bg=P["bg1"], fg=P["t2"],
                 font=app._mono_xs, width=12, anchor="w").pack(side="left")
        return widget_fn(r)

    def _combo(p, var, vals, w=12):
        c = ttk.Combobox(p, textvariable=var, values=vals,
                          width=w, font=app._mono_xs, state="readonly")
        c.pack(side="left")
        return c

    def _chk(p, var, text):
        cb = tk.Checkbutton(p, variable=var, text=text, bg=P["bg1"],
                            fg=P["t1"], selectcolor=P["bg3"],
                            activebackground=P["bg1"], font=app._mono_xs)
        cb.pack(side="left")
        return cb

    _row("Kind",     lambda p: _combo(p, app._build_kind_var,
                                       ["tarball", "installer", "portable"]))
    _row("Platform", lambda p: _combo(p, app._build_plat_var,
                                       ["auto", "linux", "macos", "windows"]))
    _row("Bump ver", lambda p: _combo(p, app._build_bump_var,
                                       ["none", "patch", "minor", "major"], w=8))
    flag_row = tk.Frame(opts_f, bg=P["bg1"])
    flag_row.pack(fill="x", pady=2)
    tk.Label(flag_row, text="Options", bg=P["bg1"], fg=P["t2"],
             font=app._mono_xs, width=12, anchor="w").pack(side="left")
    _chk(flag_row, app._build_minify_var, "minify")
    _chk(flag_row, app._build_clean_var,  "clean")
    _chk(flag_row, app._build_tests_var,  "keep tests")

    tk.Frame(parent, bg=P["line"], height=1).pack(fill="x")
    btn_row = tk.Frame(parent, bg=P["bg1"])
    btn_row.pack(fill="x", padx=14, pady=6)

    def _ab(text, cmd, accent=False):
        col = P["green"] if accent else P["t2"]
        bdr = P["green2"] if accent else P["line2"]
        b = tk.Label(btn_row, text=text, bg=P["bg3"], fg=col,
                     font=app._mono_s, padx=10, pady=4, cursor="hand2",
                     highlightbackground=bdr, highlightthickness=1)
        b.pack(side="left", padx=(0, 6))
        b.bind("<Button-1>", lambda _: cmd())
        return b

    _ab("▶ Build", app._run_build, accent=True)
    _ab("🧹 Clean", app._run_clean_only)
    _ab("📦 Archive", lambda: app._archive_version() if app.graph else None)

    # Parse perf
    tk.Frame(parent, bg=P["line"], height=1).pack(fill="x")
    tk.Label(parent, text="PARSE PERFORMANCE", bg=P["bg1"], fg=P["t3"],
             font=app._mono_xs, padx=14, pady=4, anchor="w").pack(fill="x")
    app._perf_frame = tk.Frame(parent, bg=P["bg1"])
    app._perf_frame.pack(fill="x", padx=14, pady=(0, 4))
    app._refresh_perf_display()

    # Render timing
    tk.Frame(parent, bg=P["line"], height=1).pack(fill="x")
    render_hdr = tk.Frame(parent, bg=P["bg1"])
    render_hdr.pack(fill="x")
    tk.Label(render_hdr, text="LIVE RENDER TIMING", bg=P["bg1"], fg=P["t3"],
             font=app._mono_xs, padx=14, pady=4, anchor="w").pack(side="left", fill="x", expand=True)
    live_btn = tk.Label(render_hdr, text="Start", bg=P["bg3"], fg=P["green"],
                         font=app._mono_xs, padx=6, pady=2, cursor="hand2",
                         highlightbackground=P["green2"], highlightthickness=1)
    live_btn.pack(side="right", padx=14)
    app._render_timing_frame = tk.Frame(parent, bg=P["bg1"])
    app._render_timing_frame.pack(fill="x", padx=14, pady=(0, 4))
    app._render_timing_active = False
    app._render_timing_after_id = None

    def _toggle():
        app._render_timing_active = not app._render_timing_active
        if app._render_timing_active:
            live_btn.config(text="Stop", fg=P["red"],
                            highlightbackground=P["red"])
            app._update_render_timing()
        else:
            live_btn.config(text="Start", fg=P["green"],
                            highlightbackground=P["green2"])
            if app._render_timing_after_id:
                app.after_cancel(app._render_timing_after_id)
    live_btn.bind("<Button-1>", lambda _: _toggle())

    # Build output log
    tk.Frame(parent, bg=P["line"], height=1).pack(fill="x")
    tk.Label(parent, text="BUILD OUTPUT", bg=P["bg1"], fg=P["t3"],
             font=app._mono_xs, padx=14, pady=4, anchor="w").pack(fill="x")
    log_f = tk.Frame(parent, bg=P["bg0"])
    log_f.pack(fill="both", expand=True)
    lsb = tk.Scrollbar(log_f)
    lsb.pack(side="right", fill="y")
    app._build_log_text = tk.Text(
        log_f, bg=P["bg0"], fg=P["t1"],
        font=(app._mono_xs.actual()["family"], 9),
        yscrollcommand=lsb.set, bd=0, wrap="char", state="disabled",
    )
    app._build_log_text.pack(fill="both", expand=True, padx=6, pady=4)
    lsb.config(command=app._build_log_text.yview)
    app._build_log_text.tag_config("ok",   foreground=P["green"])
    app._build_log_text.tag_config("err",  foreground=P["red"])
    app._build_log_text.tag_config("warn", foreground=P["amber"])
    app._build_log_text.tag_config("dim",  foreground=P["t2"])
    app._show_build_history()


# ═══════════════════════════════════════════════════════════════════════════════
# LOG tab
# ═══════════════════════════════════════════════════════════════════════════════

def build_log_tab(parent: tk.Frame, app) -> None:
    import tkinter as tk
    import tkinter.ttk as ttk
    from gui.log import get_log_path, recent_lines, clear_ring
    from gui.app import P, fmt_size
    from gui.app import P
    mono = app._mono_xs.actual()["family"]

    hdr = tk.Frame(parent, bg=P["bg1"])
    hdr.pack(fill="x")
    hi = tk.Frame(hdr, bg=P["bg1"])
    hi.pack(fill="x", padx=12, pady=6)
    tk.Label(hi, text=get_log_path(), bg=P["bg1"], fg=P["t3"],
             font=(mono, 8)).pack(side="left")

    for text, cmd in [("Clear", lambda: (clear_ring(), _refresh_log(app))),
                      ("↻", lambda: _refresh_log(app))]:
        b = tk.Label(hi, text=text, bg=P["bg3"], fg=P["t2"],
                     font=app._mono_xs, padx=6, pady=2, cursor="hand2",
                     highlightbackground=P["line2"], highlightthickness=1)
        b.pack(side="right", padx=2)
        b.bind("<Button-1>", lambda _, c=cmd: c())

    tk.Frame(hdr, bg=P["line"], height=1).pack(fill="x")

    log_f = tk.Frame(parent, bg=P["bg0"])
    log_f.pack(fill="both", expand=True)
    lsb = tk.Scrollbar(log_f)
    lsb.pack(side="right", fill="y")
    app._log_text = tk.Text(log_f, bg=P["bg0"], fg=P["t2"],
                             font=(mono, 9), yscrollcommand=lsb.set,
                             bd=0, state="disabled", wrap="char", padx=10)
    app._log_text.pack(fill="both", expand=True)
    lsb.config(command=app._log_text.yview)
    app._log_text.tag_config("ERROR",   foreground=P["red"])
    app._log_text.tag_config("WARNING", foreground=P["amber"])
    app._log_text.tag_config("INFO",    foreground=P["t1"])
    app._log_text.tag_config("DEBUG",   foreground=P["t3"])
    _refresh_log(app)

    def _auto():
        try:
            if app._log_text.winfo_exists():
                _refresh_log(app)
                app._log_text.after(2000, _auto)
        except Exception:
            pass
    app._log_text.after(2000, _auto)


def _refresh_log(app) -> None:
    from gui.log import recent_lines
    w = getattr(app, "_log_text", None)
    if not w:
        return
    try:
        lines = recent_lines(200)
        w.config(state="normal")
        w.delete("1.0", "end")
        for level, msg in lines:
            w.insert("end", msg + "\n", level)
        w.see("end")
        w.config(state="disabled")
    except Exception:
        pass
