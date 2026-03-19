"""
gui/dialogs_mixin.py
====================
DialogsMixin — loading overlay, status-bar updaters, version management,
process panel, log panel, and build panel.

Extracted from gui/app.py. Designed as a mixin for SIDE_App.
"""
from __future__ import annotations
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import threading
import json
try:
    from .app import P, _ROOT_DIR, cat_style, fmt_size
except (ImportError, ValueError):
    from gui.app import P, _ROOT_DIR, cat_style, fmt_size

from process.process_manager import ProcessManager
try:
    from ..monitor.perf import ProcessMonitor
    from ..version.version_manager import (
        archive_version, apply_update, list_versions, compress_loose as compress_versions
    )
    from ..build.packager import package_project, PackageOptions
    from ..build.cleaner import clean_project, CleanOptions
    from ..parser.project_config import load_project_config, save_project_config, bump_version
except (ImportError, ValueError):
    from monitor.perf import ProcessMonitor
    from version.version_manager import (
        archive_version, apply_update, list_versions, compress_loose as compress_versions
    )
    from build.packager import package_project, PackageOptions
    from build.cleaner import clean_project, CleanOptions
    from parser.project_config import load_project_config, save_project_config, bump_version

try:
    from .log import get_log_path, recent_lines, clear_ring
except (ImportError, ValueError):
    from gui.log import get_log_path, recent_lines, clear_ring


class DialogsMixin:
    """Loading overlay, status updates, version management, process/log/build panels."""

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

        # Guard: ensure attrs exist (defensive against missing __init__ calls)
        if not hasattr(self, "_loading_win"):
            self._loading_win = None
            self._loading_progress = 0
            self._loading_msg = None
            self._loading_fill = None
            self._loading_after_id = None

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
        if not hasattr(self, '_loading_win'):
            return
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
                    cfg = load_project_config(root)
                    new_ver = bump_version(cfg.get("version", "0.0.0"), bump)
                    cfg["version"] = new_ver
                    save_project_config(root, cfg)
                    self.after(0, lambda v=new_ver:
                               self._build_log(f"  Version → {v}", "ok"))
                    self._log.info("Version bumped to %s", new_ver)

            except Exception as exc:
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

