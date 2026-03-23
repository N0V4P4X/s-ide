# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
src/gui_app.py
==============
Tkinter GUI for the PEMDAS calculator.

Usage
-----
    python src/gui_app.py

Layout
------
    ┌─────────────────────────────┐
    │  3 + 4 * 2                  │  ← expression display
    │                          11 │  ← result display
    ├─────────────────────────────┤
    │  ( )  %   ←   C            │
    │  7   8   9   ÷             │
    │  4   5   6   ×             │
    │  1   2   3   −             │
    │  ±   0   .   +             │
    │  **  (    )   =            │
    └─────────────────────────────┘
"""

from __future__ import annotations
import sys
import tkinter as tk
from src.pemdas import evaluate, format_result, ParseError

# ── Colour palette (dark) ──────────────────────────────────────────────────────
BG      = "#1a1a1a"
BG_DISP = "#111111"
FG      = "#f0f0f0"
FG_DIM  = "#888888"
FG_EXPR = "#aaaaaa"
BTN_NUM = "#2a2a2a"
BTN_OPS = "#333355"
BTN_EQ  = "#1a4a2a"
BTN_CLR = "#3a1a1a"
BTN_HOV = "#404040"
ACCENT  = "#39ff8a"
RED     = "#ff5555"
FONT    = ("Courier New", 16)
FONT_SM = ("Courier New", 11)
FONT_LG = ("Courier New", 26, "bold")


class CalculatorApp:
    """
    PEMDAS-correct calculator GUI built with tkinter.

    State
    -----
    _expr: str     — current expression string being built
    _result: str   — last evaluated result (shown in result display)
    _just_eval: bool — True immediately after = is pressed;
                       next digit starts a fresh expression

    All state mutations go through _update_display().
    """

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._root.title("PEMDAS Calculator")
        self._root.configure(bg=BG)
        self._root.resizable(False, False)

        self._expr: str = ""
        self._result: str = ""
        self._just_eval: bool = False
        self._history: list[tuple[str, str]] = []

        self._build_ui()
        self._bind_keys()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Build all widgets."""
        # Display area
        disp = tk.Frame(self._root, bg=BG_DISP, padx=14, pady=10)
        disp.pack(fill="x")

        # Expression line (small, top)
        self._expr_var = tk.StringVar(value="")
        self._expr_lbl = tk.Label(
            disp, textvariable=self._expr_var,
            bg=BG_DISP, fg=FG_EXPR, font=FONT_SM,
            anchor="e", width=24,
        )
        self._expr_lbl.pack(fill="x")

        # Result line (large, bottom)
        self._result_var = tk.StringVar(value="0")
        self._result_lbl = tk.Label(
            disp, textvariable=self._result_var,
            bg=BG_DISP, fg=FG, font=FONT_LG,
            anchor="e", width=24,
        )
        self._result_lbl.pack(fill="x")

        # Button grid
        grid = tk.Frame(self._root, bg=BG, padx=4, pady=4)
        grid.pack()

        buttons: list[tuple[str, str, int]] = [
            # (label,  action,    colspan)
            ("(",    "(",       1),
            (")",    ")",       1),
            ("%",    "%",       1),
            ("⌫",   "back",    1),
            ("7",    "7",       1),
            ("8",    "8",       1),
            ("9",    "9",       1),
            ("÷",    "/",       1),
            ("4",    "4",       1),
            ("5",    "5",       1),
            ("6",    "6",       1),
            ("×",    "*",       1),
            ("1",    "1",       1),
            ("2",    "2",       1),
            ("3",    "3",       1),
            ("−",    "-",       1),
            ("±",    "negate",  1),
            ("0",    "0",       1),
            (".",    ".",       1),
            ("+",    "+",       1),
            ("xʸ",  "**",      1),
            ("C",    "clear",   2),
            ("=",    "eval",    1),
        ]

        self._btns: dict[str, tk.Label] = {}
        col, row = 0, 0
        for label, action, span in buttons:
            if action in ("eval",):
                bg = BTN_EQ
            elif action in ("clear",):
                bg = BTN_CLR
            elif action in ("/", "*", "-", "+", "**", "%", "back", "negate"):
                bg = BTN_OPS
            else:
                bg = BTN_NUM

            btn = tk.Label(
                grid, text=label, font=FONT,
                bg=bg, fg=FG,
                width=4 * span - 1, height=1,
                padx=8, pady=10,
                relief="flat", cursor="hand2",
            )
            btn.grid(row=row, column=col, columnspan=span,
                     padx=2, pady=2, sticky="nsew")
            btn.bind("<Button-1>", lambda _, a=action: self._handle(a))
            btn.bind("<Enter>",  lambda e, b=btn, c=bg: b.config(bg=BTN_HOV))
            btn.bind("<Leave>",  lambda e, b=btn, c=bg: b.config(bg=c))
            self._btns[action] = btn

            col += span
            if col >= 4:
                col = 0
                row += 1

    def _bind_keys(self) -> None:
        """Bind keyboard shortcuts."""
        self._root.bind("<Key>", self._on_key)

    def _on_key(self, event: tk.Event) -> None:
        key = event.char
        keysym = event.keysym
        if key in "0123456789.+-*/%()":
            self._handle(key)
        elif keysym in ("Return", "KP_Enter"):
            self._handle("eval")
        elif keysym == "BackSpace":
            self._handle("back")
        elif keysym == "Escape":
            self._handle("clear")
        elif key == "^":
            self._handle("**")

    # ── Action dispatch ────────────────────────────────────────────────────────

    def _handle(self, action: str) -> None:
        """Dispatch a button action."""
        if action == "clear":
            self._expr = ""
            self._result = ""
            self._just_eval = False

        elif action == "back":
            if self._just_eval:
                self._expr = ""
                self._just_eval = False
            else:
                self._expr = self._expr[:-1]

        elif action == "eval":
            if not self._expr.strip():
                return
            try:
                val = evaluate(self._expr)
                res = format_result(val)
                self._history.append((self._expr, res))
                self._result = res
                self._just_eval = True
            except ParseError as e:
                self._result_var.set(f"Error")
                self._expr_var.set(str(e)[:28])
                return

        elif action == "negate":
            # Wrap current expr in -(...)
            if self._expr:
                self._expr = f"-({self._expr})"
            else:
                self._expr = "-"

        else:
            # Digit or operator
            if self._just_eval and action not in "+-*/%()**":
                # Start fresh after eval, unless operator continues
                self._expr = ""
            self._just_eval = False
            self._expr += action

        self._update_display()

    def _update_display(self) -> None:
        """Refresh both display lines."""
        # Live preview result while typing
        preview = ""
        if self._expr and not self._just_eval:
            try:
                val = evaluate(self._expr)
                preview = format_result(val)
            except ParseError:
                preview = ""

        self._expr_var.set(self._expr[-30:] if self._expr else "")
        if self._just_eval:
            self._result_var.set(self._result)
        else:
            self._result_var.set(preview or self._expr[-12:] or "0")


def main() -> None:
    """Launch the calculator GUI."""
    root = tk.Tk()
    app = CalculatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

# ── GPLv3 interactive notice ──────────────────────────────────────────────────

_GPLv3_WARRANTY = (
    "THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY\n"
    "APPLICABLE LAW. EXCEPT WHEN OTHERWISE STATED IN WRITING THE COPYRIGHT\n"
    'HOLDERS AND/OR OTHER PARTIES PROVIDE THE PROGRAM \"AS IS\" WITHOUT\n'
    "WARRANTY OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT\n"
    "LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A\n"
    "PARTICULAR PURPOSE. THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE\n"
    "OF THE PROGRAM IS WITH YOU.  (GPL-3.0-or-later §15)"
)

_GPLv3_CONDITIONS = (
    "You may convey verbatim copies of the Program's source code as you\n"
    "receive it, in any medium, provided that you conspicuously and\n"
    "appropriately publish on each copy an appropriate copyright notice and\n"
    "disclaimer of warranty. (See GPL-3.0 §4-6 for full conditions.)\n"
    "Full license: <https://www.gnu.org/licenses/gpl-3.0.html>"
)


def gplv3_notice():
    """Print the short GPLv3 startup notice. Call this at program startup."""
    print("S-IDE  Copyright (C) 2026  N0V4-N3XU5")
    print("This program comes with ABSOLUTELY NO WARRANTY; for details type 'show w'.")
    print("This is free software, and you are welcome to redistribute it")
    print("under certain conditions; type 'show c' for details.")


def gplv3_handle(cmd: str) -> bool:
    """
    Check whether *cmd* is a GPLv3 license command and handle it.
    Returns True if the command was consumed (caller should skip normal processing).
    """
    match cmd.strip().lower():
        case "show w":
            print(_GPLv3_WARRANTY)
            return True
        case "show c":
            print(_GPLv3_CONDITIONS)
            return True
    return False

# ─────────────────────────────────────────────────────────────────────────────
