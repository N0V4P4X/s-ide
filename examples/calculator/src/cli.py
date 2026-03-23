# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
src/cli.py
==========
Command-line interface for the PEMDAS calculator.

Usage
-----
    python src/cli.py              # interactive REPL
    python src/cli.py "3 + 4 * 2" # single expression
    echo "2 ** 10" | python src/cli.py
"""

from __future__ import annotations
import sys
from src.pemdas import evaluate, format_result, ParseError


BANNER = (
    "PEMDAS Calculator  (type 'quit' or Ctrl-D to exit)\n"
    "Supports: + - * / // % ** ^ ( )\n"
    "Right-associative exponentiation: 2^3^2 = 512\n"
)

PROMPT = ">>> "


def run_repl() -> None:
    """Start an interactive REPL session."""
    print(BANNER)
    history: list[str] = []
    while True:
        try:
            line = input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("quit", "exit", "q"):
            break
        if line == "history":
            for i, h in enumerate(history, 1):
                print(f"  {i}: {h}")
            continue
        if line == "help":
            print(BANNER)
            continue
        try:
            result = evaluate(line)
            formatted = format_result(result)
            print(f"= {formatted}")
            history.append(f"{line} = {formatted}")
        except ParseError as e:
            print(f"Error: {e}", file=sys.stderr)


def run_expression(expr: str) -> int:
    """Evaluate a single expression. Returns exit code."""
    try:
        result = evaluate(expr)
        print(format_result(result))
        return 0
    except ParseError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """CLI entry point."""
    args = sys.argv[1:]

    if not sys.stdin.isatty() and not args:
        exit_code = 0
        for line in sys.stdin:
            line = line.strip()
            if line:
                exit_code = run_expression(line) or exit_code
        return exit_code

    if args:
        return run_expression(" ".join(args))

    run_repl()
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
