"""
gui/editor.py
=============
Lightweight syntax-highlighted source editor built on tk.Text.

Features
--------
- Token-based syntax highlighting (Python, JS/TS, JSON, shell)
- Line numbers gutter
- Current-line highlight
- Find/replace bar (Ctrl+F / Cmd+F)
- Unsaved changes indicator
- Read-only mode (default when opened from node click)
- Save shortcut (Ctrl+S) when in edit mode
- "Ask AI about this file" button when AI is configured

The editor is opened as a Toplevel window from the node graph
(double-click on a node) or from the inspector panel.
It does NOT auto-save; changes require explicit Ctrl+S or Save button.
"""

from __future__ import annotations
import os
import re
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox
from typing import Callable


# ── Token patterns per language ───────────────────────────────────────────────

_PY_TOKENS = [
    ("keyword",   r"\b(False|None|True|and|as|assert|async|await|break|class"
                  r"|continue|def|del|elif|else|except|finally|for|from|global"
                  r"|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return"
                  r"|try|while|with|yield)\b"),
    ("builtin",   r"\b(abs|all|any|bin|bool|bytes|callable|chr|dict|dir|divmod"
                  r"|enumerate|eval|exec|filter|float|format|frozenset|getattr"
                  r"|globals|hasattr|hash|help|hex|id|input|int|isinstance"
                  r"|issubclass|iter|len|list|locals|map|max|min|next|object"
                  r"|oct|open|ord|pow|print|property|range|repr|reversed|round"
                  r"|set|setattr|slice|sorted|staticmethod|str|sum|super|tuple"
                  r"|type|vars|zip)\b"),
    ("string3",   r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\''),
    ("string",    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''),
    ("comment",   r"#[^\n]*"),
    ("decorator", r"@[\w.]+"),
    ("number",    r"\b\d+\.?\d*([eE][+-]?\d+)?\b|0x[0-9a-fA-F]+"),
    ("type_hint", r":\s*[\w\[\], |]+(?=\s*[=,\)\n])"),
    ("funcname",  r"(?<=def )\w+"),
    ("classname", r"(?<=class )\w+"),
]

_JS_TOKENS = [
    ("keyword",   r"\b(break|case|catch|class|const|continue|debugger|default"
                  r"|delete|do|else|export|extends|finally|for|from|function"
                  r"|if|import|in|instanceof|let|new|of|return|static|super"
                  r"|switch|this|throw|try|typeof|var|void|while|with|yield"
                  r"|async|await|interface|type|enum|namespace|implements)\b"),
    ("string3",   r'`[\s\S]*?`'),
    ("string",    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''),
    ("comment",   r"//[^\n]*|/\*[\s\S]*?\*/"),
    ("decorator", r"@[\w.]+"),
    ("number",    r"\b\d+\.?\d*\b|0x[0-9a-fA-F]+"),
    ("funcname",  r"(?<=function )\w+"),
    ("classname", r"(?<=class )\w+"),
]

_JSON_TOKENS = [
    ("string",  r'"(?:\\.|[^"\\])*"'),
    ("number",  r"-?\d+\.?\d*([eE][+-]?\d+)?"),
    ("keyword", r"\b(true|false|null)\b"),
]

_SH_TOKENS = [
    ("comment", r"#[^\n]*"),
    ("string",  r'"(?:\\.|[^"\\])*"|\'[^\']*\''),
    ("keyword", r"\b(if|then|else|elif|fi|for|while|do|done|case|esac"
                r"|function|return|export|source|local|readonly)\b"),
    ("builtin", r"\b(echo|printf|cd|ls|mkdir|rm|cp|mv|grep|sed|awk|cat"
                r"|find|exit|set|unset|shift|read|test)\b"),
]

_LANG_TOKENS = {
    ".py":    _PY_TOKENS,
    ".pyw":   _PY_TOKENS,
    ".js":    _JS_TOKENS,
    ".mjs":   _JS_TOKENS,
    ".cjs":   _JS_TOKENS,
    ".ts":    _JS_TOKENS,
    ".tsx":   _JS_TOKENS,
    ".jsx":   _JS_TOKENS,
    ".json":  _JSON_TOKENS,
    ".sh":    _SH_TOKENS,
    ".bash":  _SH_TOKENS,
}

# Colours (dark theme, consistent with app palette)
_TOKEN_COLOURS = {
    "keyword":   "#c792ea",   # purple
    "builtin":   "#82aaff",   # blue
    "string":    "#c3e88d",   # green string
    "string3":   "#c3e88d",
    "comment":   "#546e7a",   # muted blue-grey
    "decorator": "#ffcb6b",   # amber
    "number":    "#f78c6c",   # orange
    "type_hint": "#89ddff",   # cyan
    "funcname":  "#82aaff",   # blue
    "classname": "#ffcb6b",   # amber
    "default":   "#d0d0e8",
}


def _compile_patterns(ext: str) -> list[tuple[str, re.Pattern]]:
    tokens = _LANG_TOKENS.get(ext.lower(), [])
    return [(name, re.compile(pat, re.MULTILINE)) for name, pat in tokens]


# ── Editor window ─────────────────────────────────────────────────────────────

class EditorWindow:
    """
    Standalone Toplevel editor for a single source file.
    Opens in read-only mode by default; toggle with the Edit button.
    """

    def __init__(
        self,
        master,
        filepath:    str,
        project_root: str = "",
        read_only:   bool = True,
        on_save:     Callable[[str, str], None] | None = None,
        on_ask_ai:   Callable[[str, str], None] | None = None,
        theme:       dict | None = None,
    ):
        self.filepath     = os.path.abspath(filepath)
        self.project_root = project_root or os.path.dirname(filepath)
        self.rel_path     = os.path.relpath(filepath, self.project_root)
        self.read_only    = read_only
        self.on_save      = on_save
        self.on_ask_ai    = on_ask_ai
        self._modified    = False
        self._patterns: list[tuple[str, re.Pattern]] = []
        self._rehighlight_after: str | None = None

        T = theme or {}
        self.bg0   = T.get("bg0",   "#060608")
        self.bg1   = T.get("bg1",   "#0c0c10")
        self.bg2   = T.get("bg2",   "#111116")
        self.bg3   = T.get("bg3",   "#18181f")
        self.green = T.get("green", "#39ff8a")
        self.amber = T.get("amber", "#ffaa33")
        self.red   = T.get("red",   "#ff4455")
        self.t0    = T.get("t0",    "#e2e2f0")
        self.t1    = T.get("t1",    "#9090a8")
        self.t2    = T.get("t2",    "#55556a")
        self.line  = T.get("line",  "#1c1c24")
        self.line2 = T.get("line2", "#262632")

        self._build(master)
        self._load_file()

    # ── Build UI ───────────────────────────────────────────────────────────────

    def _build(self, master):
        self.win = tk.Toplevel(master)
        self.win.title(f"{self.rel_path}")
        self.win.configure(bg=self.bg0)
        self.win.geometry("900x680")
        self.win.resizable(True, True)
        self.win.transient(master)

        self._build_toolbar()
        self._build_find_bar()
        self._build_editor_area()
        self._bind_shortcuts()

    def _build_toolbar(self):
        tb = tk.Frame(self.win, bg=self.bg1)
        tb.pack(fill="x")
        tk.Frame(tb, bg=self.line, height=1).pack(fill="x")
        inner = tk.Frame(tb, bg=self.bg1)
        inner.pack(fill="x", padx=10, pady=6)

        # File path label
        self._title_var = tk.StringVar(value=self.rel_path)
        tk.Label(inner, textvariable=self._title_var, bg=self.bg1, fg=self.t0,
                 font=self._mono(10, bold=True)).pack(side="left")

        self._modified_lbl = tk.Label(inner, text="", bg=self.bg1,
                                       fg=self.amber, font=self._mono(9))
        self._modified_lbl.pack(side="left", padx=6)

        # Right buttons
        if self.on_ask_ai:
            self._btn(inner, "✦ Ask AI", self.amber, self._ask_ai).pack(side="right", padx=3)
        self._save_btn = self._btn(inner, "💾 Save", self.green, self._save)
        self._save_btn.pack(side="right", padx=3)
        self._edit_btn = self._btn(inner, "✎ Edit", self.t1, self._toggle_edit)
        self._edit_btn.pack(side="right", padx=3)
        self._btn(inner, "⌕ Find", self.t1, self._toggle_find).pack(side="right", padx=3)

        tk.Frame(tb, bg=self.line, height=1).pack(fill="x")

    def _build_find_bar(self):
        self._find_bar = tk.Frame(self.win, bg=self.bg2)
        # Not packed by default
        fb_inner = tk.Frame(self._find_bar, bg=self.bg2)
        fb_inner.pack(fill="x", padx=10, pady=5)

        tk.Label(fb_inner, text="Find:", bg=self.bg2, fg=self.t1,
                 font=self._mono(9)).pack(side="left")
        self._find_var = tk.StringVar()
        self._find_entry = tk.Entry(
            fb_inner, textvariable=self._find_var, bg=self.bg3,
            fg=self.t0, insertbackground=self.green, bd=0,
            font=self._mono(10), width=24)
        self._find_entry.pack(side="left", padx=(4, 8), ipady=2)
        self._find_entry.bind("<Return>", lambda _: self._find_next())
        self._find_entry.bind("<Escape>", lambda _: self._toggle_find())

        self._btn(fb_inner, "↓ Next", self.t1, self._find_next).pack(side="left", padx=2)
        self._btn(fb_inner, "↑ Prev", self.t1, self._find_prev).pack(side="left", padx=2)

        tk.Label(fb_inner, text="Replace:", bg=self.bg2, fg=self.t1,
                 font=self._mono(9)).pack(side="left", padx=(10, 0))
        self._replace_var = tk.StringVar()
        tk.Entry(fb_inner, textvariable=self._replace_var, bg=self.bg3,
                 fg=self.t0, insertbackground=self.green, bd=0,
                 font=self._mono(10), width=20).pack(side="left", padx=4, ipady=2)
        self._btn(fb_inner, "Replace", self.amber, self._replace_one).pack(side="left", padx=2)
        self._btn(fb_inner, "All", self.amber, self._replace_all).pack(side="left", padx=2)

        self._find_count_var = tk.StringVar(value="")
        tk.Label(fb_inner, textvariable=self._find_count_var, bg=self.bg2,
                 fg=self.t1, font=self._mono(8)).pack(side="left", padx=8)

    def _build_editor_area(self):
        frame = tk.Frame(self.win, bg=self.bg0)
        frame.pack(fill="both", expand=True)

        # Vertical scrollbar
        vscroll = tk.Scrollbar(frame, orient="vertical")
        vscroll.pack(side="right", fill="y")
        hscroll = tk.Scrollbar(frame, orient="horizontal")
        hscroll.pack(side="bottom", fill="x")

        # Line numbers
        self._lnum_canvas = tk.Canvas(frame, bg=self.bg1, width=48,
                                       highlightthickness=0)
        self._lnum_canvas.pack(side="left", fill="y")

        # Main text widget
        font = self._mono(10)
        self._text = tk.Text(
            frame,
            bg=self.bg0, fg=_TOKEN_COLOURS["default"],
            insertbackground=self.green,
            selectbackground="#1a3a5a", selectforeground=self.t0,
            font=font, bd=0, wrap="none",
            undo=True, maxundo=200,
            yscrollcommand=self._on_yscroll,
            xscrollcommand=hscroll.set,
            state="disabled",
        )
        self._text.pack(side="left", fill="both", expand=True)
        vscroll.config(command=self._text.yview)
        hscroll.config(command=self._text.xview)

        # Configure syntax-highlight tags
        for tok, colour in _TOKEN_COLOURS.items():
            self._text.tag_configure(tok, foreground=colour)
        self._text.tag_configure("current_line", background=self.bg2)
        self._text.tag_configure("find_match",
                                  background="#4a3a00", foreground="#ffdd00")
        self._text.tag_configure("find_current",
                                  background="#ffaa00", foreground="#000000")

        # Bind text events
        self._text.bind("<<Modified>>", self._on_text_modified)
        self._text.bind("<KeyRelease>", self._on_key)
        self._text.bind("<ButtonRelease-1>", self._update_line_numbers)

    def _on_yscroll(self, *args):
        """Keep line numbers in sync."""
        if hasattr(self, "_lnum_canvas"):
            # Defer to allow text to update first
            self.win.after(1, self._update_line_numbers)
        tk.Scrollbar.set(self._text.master.children.get("!scrollbar", tk.Scrollbar()), *args)
        # Actually delegate properly:
        for widget in self._text.master.winfo_children():
            if isinstance(widget, tk.Scrollbar) and widget.cget("orient") == "vertical":
                widget.set(*args)
                break

    # ── File I/O ──────────────────────────────────────────────────────────────

    def _load_file(self):
        ext = os.path.splitext(self.filepath)[1].lower()
        self._patterns = _compile_patterns(ext)
        try:
            content = open(self.filepath, "r", encoding="utf-8", errors="replace").read()
        except Exception as e:
            content = f"# Error reading file: {e}"
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("1.0", content)
        self._text.edit_reset()
        self._modified = False
        self._apply_state()
        self._highlight_all()
        self._update_line_numbers()

    def _save(self):
        if self.read_only:
            return
        try:
            content = self._text.get("1.0", "end-1c")
            with open(self.filepath, "w", encoding="utf-8") as f:
                f.write(content)
            self._modified = False
            self._modified_lbl.config(text="")
            self.win.title(self.rel_path)
            if self.on_save:
                self.on_save(self.filepath, content)
        except Exception as e:
            messagebox.showerror("Save Error", str(e), parent=self.win)

    # ── Edit / read-only toggle ────────────────────────────────────────────────

    def _toggle_edit(self):
        self.read_only = not self.read_only
        self._apply_state()

    def _apply_state(self):
        state = "normal" if not self.read_only else "disabled"
        self._text.config(state=state)
        col = self.t1 if self.read_only else self.green
        self._edit_btn.config(text="✎ Edit" if self.read_only else "● Editing", fg=col)

    # ── Find / replace ─────────────────────────────────────────────────────────

    def _toggle_find(self):
        if self._find_bar.winfo_ismapped():
            self._find_bar.pack_forget()
        else:
            self._find_bar.pack(fill="x", after=self.win.winfo_children()[0])
            self._find_entry.focus_set()

    def _find_all(self, query: str) -> list[str]:
        self._text.tag_remove("find_match", "1.0", "end")
        self._text.tag_remove("find_current", "1.0", "end")
        if not query:
            self._find_count_var.set("")
            return []
        positions = []
        start = "1.0"
        while True:
            pos = self._text.search(query, start, "end", nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(query)}c"
            self._text.tag_add("find_match", pos, end)
            positions.append(pos)
            start = end
        self._find_count_var.set(f"{len(positions)} matches" if positions else "no matches")
        return positions

    def _find_next(self):
        query = self._find_var.get()
        positions = self._find_all(query)
        if not positions:
            return
        # Find first match after cursor
        cursor = self._text.index("insert")
        target = next((p for p in positions if self._text.compare(p, ">", cursor)), positions[0])
        self._goto(target, len(query))

    def _find_prev(self):
        query = self._find_var.get()
        positions = self._find_all(query)
        if not positions:
            return
        cursor = self._text.index("insert")
        before = [p for p in positions if self._text.compare(p, "<", cursor)]
        target = before[-1] if before else positions[-1]
        self._goto(target, len(query))

    def _goto(self, pos: str, length: int = 0):
        self._text.tag_remove("find_current", "1.0", "end")
        end = f"{pos}+{length}c"
        self._text.tag_add("find_current", pos, end)
        self._text.mark_set("insert", pos)
        self._text.see(pos)

    def _replace_one(self):
        if self.read_only:
            return
        query   = self._find_var.get()
        replace = self._replace_var.get()
        pos = self._text.search(query, "insert", "end", nocase=True)
        if pos:
            self._text.delete(pos, f"{pos}+{len(query)}c")
            self._text.insert(pos, replace)
            self._find_next()

    def _replace_all(self):
        if self.read_only:
            return
        query   = self._find_var.get()
        replace = self._replace_var.get()
        content = self._text.get("1.0", "end-1c")
        new_content = re.sub(re.escape(query), replace, content, flags=re.IGNORECASE)
        if new_content != content:
            self._text.config(state="normal")
            self._text.delete("1.0", "end")
            self._text.insert("1.0", new_content)
            self._apply_state()
            self._highlight_all()

    # ── Syntax highlighting ────────────────────────────────────────────────────

    def _highlight_all(self):
        """Apply all token highlighting to the entire buffer."""
        content = self._text.get("1.0", "end-1c")
        # Remove all token tags
        for tok in _TOKEN_COLOURS:
            self._text.tag_remove(tok, "1.0", "end")
        # Apply patterns in order (earlier = higher priority)
        applied: list[tuple[int, int]] = []   # occupied ranges
        for name, pattern in self._patterns:
            for m in pattern.finditer(content):
                s, e = m.start(), m.end()
                # Don't overlap already-highlighted regions
                if any(a <= s < b or a < e <= b for a, b in applied):
                    continue
                start = f"1.0+{s}c"
                end   = f"1.0+{e}c"
                self._text.tag_add(name, start, end)
                applied.append((s, e))

    def _schedule_highlight(self):
        """Debounced rehighlight — fires 300ms after last keystroke."""
        if self._rehighlight_after:
            self.win.after_cancel(self._rehighlight_after)
        self._rehighlight_after = self.win.after(300, self._highlight_all)

    # ── Line numbers ───────────────────────────────────────────────────────────

    def _update_line_numbers(self, *_):
        c  = self._lnum_canvas
        c.delete("all")
        font = self._mono(9)
        i    = self._text.index("@0,0")
        while True:
            dline = self._text.dlineinfo(i)
            if dline is None:
                break
            y    = dline[1]
            line = int(i.split(".")[0])
            c.create_text(40, y + 2, text=str(line), anchor="ne",
                          fill=self.t2, font=font)
            i = self._text.index(f"{i}+1line")
            if self._text.compare(i, ">=", "end"):
                break
        # Current line highlight
        try:
            cur_line = int(self._text.index("insert").split(".")[0])
            dline = self._text.dlineinfo(f"{cur_line}.0")
            if dline:
                self._text.tag_remove("current_line", "1.0", "end")
                self._text.tag_add("current_line",
                                    f"{cur_line}.0", f"{cur_line}.end+1c")
        except Exception:
            pass

    # ── Events ─────────────────────────────────────────────────────────────────

    def _on_text_modified(self, _event=None):
        if self._text.edit_modified():
            if not self._modified:
                self._modified = True
                self._modified_lbl.config(text="● unsaved")
                self.win.title(f"● {self.rel_path}")
            self._text.edit_modified(False)

    def _on_key(self, _event=None):
        self._update_line_numbers()
        self._schedule_highlight()

    def _ask_ai(self):
        if self.on_ask_ai:
            content = self._text.get("1.0", "end-1c")
            self.on_ask_ai(self.filepath, content)

    # ── Shortcuts ──────────────────────────────────────────────────────────────

    def _bind_shortcuts(self):
        self.win.bind("<Control-s>", lambda _: self._save())
        self.win.bind("<Command-s>", lambda _: self._save())
        self.win.bind("<Control-f>", lambda _: self._toggle_find())
        self.win.bind("<Command-f>", lambda _: self._toggle_find())
        self.win.bind("<Escape>",    lambda _: self._toggle_find()
                                               if self._find_bar.winfo_ismapped()
                                               else None)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _mono(self, size: int, bold: bool = False) -> tkfont.Font:
        families = ["JetBrains Mono", "Fira Code", "Cascadia Code",
                    "Consolas", "Menlo", "DejaVu Sans Mono", "Courier New"]
        avail = set(tkfont.families())
        for fam in families:
            if fam in avail:
                return tkfont.Font(family=fam, size=size,
                                   weight="bold" if bold else "normal")
        return tkfont.Font(family="Courier", size=size,
                           weight="bold" if bold else "normal")

    def _btn(self, parent, text: str, colour: str, cmd) -> tk.Label:
        b = tk.Label(parent, text=text, bg=self.bg3, fg=colour,
                     font=self._mono(9), padx=7, pady=2, cursor="hand2",
                     highlightbackground=self.line2, highlightthickness=1)
        b.bind("<Button-1>", lambda _: cmd())
        b.bind("<Enter>", lambda _, w=b: w.config(bg=self.bg2))
        b.bind("<Leave>", lambda _, w=b: w.config(bg=self.bg3))
        return b

    def scroll_to_line(self, line: int):
        """Programmatically scroll to a specific line number."""
        self._text.see(f"{line}.0")
        self._text.mark_set("insert", f"{line}.0")
        self._update_line_numbers()

    def get_content(self) -> str:
        return self._text.get("1.0", "end-1c")

    def focus(self):
        self.win.lift()
        self.win.focus_set()
