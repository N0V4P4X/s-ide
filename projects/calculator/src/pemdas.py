"""
src/pemdas.py
=============
PEMDAS-correct expression evaluator.

Implements a recursive-descent parser that respects operator precedence:
  P — Parentheses
  E — Exponents        (right-associative)
  M — Multiplication   \\
  D — Division          > left-to-right
  A — Addition          \\
  S — Subtraction        > left-to-right

No eval() is used. The tokeniser handles integers, floats, and the
operators + - * / // % ** and parentheses.

Usage
-----
    from src.pemdas import evaluate, tokenise

    result = evaluate("3 + 4 * 2")      # 11.0
    result = evaluate("(3 + 4) * 2")    # 14.0
    result = evaluate("2 ** 3 ** 2")    # 512.0  (right-assoc)
    result = evaluate("10 / 2 + 3")     # 8.0
"""

from __future__ import annotations
import re
from typing import Iterator

# ── Token types ───────────────────────────────────────────────────────────────

_TOK_RE = re.compile(
    r"\s*(?:"
    r"(\d+\.?\d*)"          # NUMBER
    r"|(\*\*|//|[+\-*/%^()])"  # OPERATOR or PAREN
    r")\s*"
)


class ParseError(ValueError):
    """Raised when the expression cannot be parsed."""


def tokenise(expr: str) -> list[str]:
    """
    Convert an expression string to a list of tokens.

    Tokens are strings: number literals, operators, parentheses.
    Raises ParseError on unexpected characters.

    Args:
        expr: Infix mathematical expression string.

    Returns:
        Ordered list of token strings.

    Raises:
        ParseError: If an unexpected character is found.
    """
    tokens: list[str] = []
    pos = 0
    while pos < len(expr):
        if expr[pos].isspace():
            pos += 1
            continue
        m = _TOK_RE.match(expr, pos)
        if not m:
            raise ParseError(f"Unexpected character at position {pos}: {expr[pos]!r}")
        token = m.group(1) or m.group(2)
        # Normalise caret to ** for convenience
        if token == "^":
            token = "**"
        tokens.append(token)
        pos = m.end()
    return tokens


# ── Recursive descent parser ──────────────────────────────────────────────────

class _Parser:
    """
    Recursive-descent parser for infix arithmetic expressions.

    Grammar (in order of increasing precedence):
        expr        → add_sub
        add_sub     → mul_div  (('+' | '-') mul_div)*
        mul_div     → unary    (('*' | '/' | '//' | '%') unary)*
        unary       → '-' unary | exponent
        exponent    → atom ('**' unary)*     ← right-associative
        atom        → NUMBER | '(' expr ')'
    """

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self._pos = 0

    @property
    def _current(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _consume(self) -> str:
        tok = self._current
        if tok is None:
            raise ParseError("Unexpected end of expression")
        self._pos += 1
        return tok

    def _expect(self, value: str) -> None:
        tok = self._consume()
        if tok != value:
            raise ParseError(f"Expected {value!r}, got {tok!r}")

    # ── Grammar rules ─────────────────────────────────────────────────────────

    def parse(self) -> float:
        result = self._add_sub()
        if self._current is not None:
            raise ParseError(f"Unexpected token: {self._current!r}")
        return result

    def _add_sub(self) -> float:
        left = self._mul_div()
        while self._current in ("+", "-"):
            op = self._consume()
            right = self._mul_div()
            left = left + right if op == "+" else left - right
        return left

    def _mul_div(self) -> float:
        left = self._unary()
        while self._current in ("*", "/", "//", "%"):
            op = self._consume()
            right = self._unary()
            if op == "*":
                left = left * right
            elif op == "/":
                if right == 0:
                    raise ParseError("Division by zero")
                left = left / right
            elif op == "//":
                if right == 0:
                    raise ParseError("Integer division by zero")
                left = float(int(left) // int(right))
            else:  # %
                if right == 0:
                    raise ParseError("Modulo by zero")
                left = left % right
        return left

    def _unary(self) -> float:
        if self._current == "-":
            self._consume()
            return -self._unary()
        if self._current == "+":
            self._consume()
            return self._unary()
        return self._exponent()

    def _exponent(self) -> float:
        """Right-associative: 2 ** 3 ** 2 == 2 ** (3 ** 2) == 512."""
        base = self._atom()
        if self._current == "**":
            self._consume()
            exp = self._unary()   # right-assoc: recurse into unary not exponent
            return base ** exp
        return base

    def _atom(self) -> float:
        tok = self._current
        if tok is None:
            raise ParseError("Unexpected end of expression")
        if tok == "(":
            self._consume()
            val = self._add_sub()
            self._expect(")")
            return val
        # Try to parse as a number
        try:
            self._consume()
            return float(tok)
        except ValueError:
            raise ParseError(f"Expected number or '(', got {tok!r}")


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate(expr: str) -> float:
    """
    Evaluate an infix arithmetic expression with correct PEMDAS precedence.

    Supports: integers, floats, +, -, *, /, //, %, **, ^, parentheses,
    unary minus and plus.

    Args:
        expr: Expression string, e.g. "3 + 4 * 2" or "(1 + 2) ** 3".

    Returns:
        Result as a float.

    Raises:
        ParseError: If the expression is malformed.
        ParseError: If division by zero is attempted.
    """
    if not expr or not expr.strip():
        raise ParseError("Empty expression")
    tokens = tokenise(expr)
    if not tokens:
        raise ParseError("Empty expression")
    return _Parser(tokens).parse()


def format_result(value: float) -> str:
    """
    Format a float result for display.

    Returns an integer string if the value is whole (e.g. 6.0 → '6'),
    otherwise a float string with trailing zeros stripped.

    Args:
        value: Float value to format.

    Returns:
        Formatted string.
    """
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    # Strip trailing zeros after decimal
    s = f"{value:.10f}".rstrip("0").rstrip(".")
    return s
