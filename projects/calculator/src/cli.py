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
