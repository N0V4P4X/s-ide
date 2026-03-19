"""
gui/markdown.py
===============
Markdown-to-Tk-Text rendering. No tkinter import at module level —
the Text widget is accepted as an argument, so this module can be
imported in tests without a display.
"""
from __future__ import annotations
import re


def _insert_inline(w: tk.Text, text: str) -> None:
    """
    Insert a line into Text widget, applying bold/italic/inline-code tags.
    Pattern: **bold**, *italic*, `code`
    """
    # Tokenise: split on ** *  ` delimiters
    pattern = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)")
    parts = pattern.split(text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            w.insert("end", part[2:-2], "strong")
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            w.insert("end", part[1:-1], "em")
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            w.insert("end", part[1:-1], "code")
        else:
            w.insert("end", part, "ai")


def ai_append_markdown(app, text: str) -> None:
    """
    Render markdown text into the AI conversation widget.
    Handles: # headers, **bold**, *italic*, `inline code`,
             ```code blocks```, - bullet lists, numbered lists.
    """
    w = getattr(app, "_ai_conv", None)
    if not w:
        return

    lines = text.split("\n")
    in_code = False
    code_buf: list[str] = []
    lang = ""

    try:
        w.config(state="normal")

        for line in lines:
            # ── Code block toggle ─────────────────────────────────────────────
            if line.startswith("```"):
                if in_code:
                    block = "\n".join(code_buf)
                    w.insert("end", block + "\n", "code")
                    code_buf = []
                    in_code = False
                    lang = ""
                else:
                    lang = line[3:].strip()
                    in_code = True
                continue

            if in_code:
                code_buf.append(line)
                continue

            # ── Headers ───────────────────────────────────────────────────────
            if line.startswith("### "):
                w.insert("end", line[4:] + "\n", "h3")
            elif line.startswith("## "):
                w.insert("end", line[3:] + "\n", "h2")
            elif line.startswith("# "):
                w.insert("end", line[2:] + "\n", "h1")
            # ── Bullet lists ─────────────────────────────────────────────────
            elif re.match(r"^[-*+] ", line):
                w.insert("end", "  • ", "bullet")
                _insert_inline(w, line[2:])
                w.insert("end", "\n", "ai")
            elif re.match(r"^\d+\. ", line):
                num, rest = line.split(". ", 1)
                w.insert("end", f"  {num}. ", "bullet")
                _insert_inline(w, rest)
                w.insert("end", "\n", "ai")
            # ── Horizontal rule ───────────────────────────────────────────────
            elif re.match(r"^-{3,}$|^\*{3,}$", line.strip()):
                w.insert("end", "─" * 60 + "\n", "dim")
            # ── Normal line ───────────────────────────────────────────────────
            else:
                _insert_inline(w, line)
                w.insert("end", "\n", "ai")

        # Flush any unclosed code block
        if code_buf:
            w.insert("end", "\n".join(code_buf) + "\n", "code")

        w.see("end")
    finally:
        w.config(state="disabled")

