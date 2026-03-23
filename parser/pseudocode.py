# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
parser/pseudocode.py
====================
Translates Python AST into a simplified, human-readable pseudocode.
Focuses on logic flow and high-level intent.
"""

import ast
import os
from typing import List, Union, Iterable


class PseudocodeGenerator(ast.NodeVisitor):
    def __init__(self, indent_out: str = "    "):
        self.indent_out = indent_out
        self.level = 0
        self.output: List[str] = []

    def _add(self, text: str):
        self.output.append((self.indent_out * self.level) + text)

    def _visit_block(self, nodes: Iterable[ast.AST]):
        self.level += 1
        for node in nodes:
            self.visit(node)
        self.level -= 1

    def generic_visit(self, node: ast.AST):
        # Fallback for unhandled nodes
        pass

    def visit_Module(self, node: ast.Module):
        for stmt in node.body:
            self.visit(stmt)

    def visit_FunctionDef(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]):
        args = [a.arg for a in node.args.args]
        arg_str = f" with inputs ({', '.join(args)})" if args else ""
        prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        self._add(f"{prefix}define action {node.name}{arg_str}:")
        self._visit_block(node.body)
        self._add("")

    def visit_ClassDef(self, node: ast.ClassDef):
        base_str = f" based on {', '.join([self._expr(b) for b in node.bases])}" if node.bases else ""
        self._add(f"define blueprint {node.name}{base_str}:")
        self._visit_block(node.body)
        self._add("")

    def visit_If(self, node: ast.If):
        cond = self._expr(node.test)
        self._add(f"if {cond}:")
        self._visit_block(node.body)
        if node.orelse:
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                # elif case
                self.output[-1] = self.output[-1].rstrip(":")
                # Simple elif handling:
                self._add("otherwise if:")
                self.visit(node.orelse[0])
            else:
                self._add("otherwise:")
                self._visit_block(node.orelse)

    def visit_For(self, node: ast.For):
        target = self._expr(node.target)
        iter_obj = self._expr(node.iter)
        self._add(f"for each {target} in {iter_obj}:")
        self._visit_block(node.body)

    def visit_While(self, node: ast.While):
        cond = self._expr(node.test)
        self._add(f"repeat while {cond}:")
        self._visit_block(node.body)

    def visit_With(self, node: ast.With):
        items = [f"{self._expr(i.context_expr)} as {self._expr(i.optional_vars)}" if i.optional_vars else self._expr(i.context_expr) for i in node.items]
        self._add(f"using {', '.join(items)}:")
        self._visit_block(node.body)

    def visit_Try(self, node: ast.Try):
        self._add("try to:")
        self._visit_block(node.body)
        for handler in node.handlers:
            exc = self._expr(handler.type) if handler.type else "any error"
            name = f" as {handler.name}" if handler.name else ""
            self._add(f"if {exc}{name} occurs:")
            self._visit_block(handler.body)
        if node.finalbody:
            self._add("finally always:")
            self._visit_block(node.finalbody)

    def visit_Assign(self, node: ast.Assign):
        targets = [self._expr(t) for t in node.targets]
        value = self._expr(node.value)
        self._add(f"set {', '.join(targets)} to {value}")

    def visit_AugAssign(self, node: ast.AugAssign):
        target = self._expr(node.target)
        op = type(node.op).__name__
        value = self._expr(node.value)
        self._add(f"update {target} by {op} {value}")

    def visit_Return(self, node: ast.Return):
        val = self._expr(node.value) if node.value else "nothing"
        self._add(f"return {val}")

    def visit_Expr(self, node: ast.Expr):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            # Probably a docstring or stranded string, ignore
            return
        self._add(self._expr(node.value))

    def visit_Delete(self, node: ast.Delete):
        targets = [self._expr(t) for t in node.targets]
        self._add(f"delete {', '.join(targets)}")

    def visit_Raise(self, node: ast.Raise):
        exc = self._expr(node.exc) if node.exc else "current error"
        self._add(f"raise {exc}")

    def visit_Assert(self, node: ast.Assert):
        cond = self._expr(node.test)
        msg = f" with message {self._expr(node.msg)}" if node.msg else ""
        self._add(f"ensure {cond}{msg}")

    def visit_Import(self, node: ast.Import):
        pass # Ignore imports in pseudocode

    def visit_ImportFrom(self, node: ast.ImportFrom):
        pass # Ignore imports in pseudocode

    def _expr(self, node: Union[ast.AST, None]) -> str:
        if node is None:
            return ""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            return repr(node.value)
        if isinstance(node, ast.Attribute):
            return f"{self._expr(node.value)}.{node.attr}"
        if isinstance(node, ast.Call):
            args = [self._expr(a) for a in node.args]
            kwargs = [f"{k.arg}={self._expr(k.value)}" for k in node.keywords]
            all_args = args + kwargs
            return f"execute {self._expr(node.func)}({', '.join(all_args)})"
        if isinstance(node, ast.BinOp):
            return f"({self._expr(node.left)} {self._op(node.op)} {self._expr(node.right)})"
        if isinstance(node, ast.UnaryOp):
            return f"{self._op(node.op)}{self._expr(node.operand)}"
        if isinstance(node, ast.Compare):
            left = self._expr(node.left)
            ops = [self._op(o) for o in node.ops]
            comps = [self._expr(c) for c in node.comparators]
            parts = [left]
            for o, c in zip(ops, comps):
                parts.append(o)
                parts.append(c)
            return f"({' '.join(parts)})"
        if isinstance(node, ast.BoolOp):
            op = " and " if isinstance(node.op, ast.And) else " or "
            return f"({op.join([self._expr(v) for v in node.values])})"
        if isinstance(node, ast.Subscript):
            return f"{self._expr(node.value)}[{self._expr(node.slice)}]"
        if isinstance(node, ast.List):
            return f"[{', '.join([self._expr(e) for e in node.elts])}]"
        if isinstance(node, ast.Tuple):
            return f"({', '.join([self._expr(e) for e in node.elts])})"
        if isinstance(node, ast.Dict):
            items = []
            for k, v in zip(node.keys, node.values):
                items.append(f"{self._expr(k)}: {self._expr(v)}")
            return f"{{{', '.join(items)}}}"
        if isinstance(node, ast.Lambda):
            args = [a.arg for a in node.args.args]
            return f"lambda ({', '.join(args)}) -> {self._expr(node.body)}"
        if isinstance(node, ast.JoinedStr):
            return f"f'{{''.join([self._expr(v) for v in node.values])}}'"
        if isinstance(node, ast.FormattedValue):
            return f"{{{self._expr(node.value)}}}"
        if isinstance(node, ast.IfExp):
            return f"({self._expr(node.body)} if {self._expr(node.test)} else {self._expr(node.orelse)})"
        
        return f"<{type(node).__name__}>"

    def _op(self, op: ast.AST) -> str:
        ops = {
            ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
            ast.Mod: "%", ast.Pow: "**", ast.LShift: "<<", ast.RShift: ">>",
            ast.BitOr: "|", ast.BitXor: "^", ast.BitAnd: "&", ast.FloorDiv: "//",
            ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
            ast.Gt: ">", ast.GtE: ">=", ast.Is: "is", ast.IsNot: "is not",
            ast.In: "in", ast.NotIn: "not in",
            ast.Not: "not ", ast.UAdd: "+", ast.USub: "-",
        }
        return ops.get(type(op), "?")


def generate_pseudocode(source: str) -> str:
    """Convenience function to generate pseudocode from source string."""
    try:
        tree = ast.parse(source)
    except Exception as e:
        return f"# ERROR Parsing source: {e}"
    
    gen = PseudocodeGenerator()
    gen.visit(tree)
    return "\n".join(gen.output)


def translate_file(path: str) -> str:
    """Load a file and return its pseudocode."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return generate_pseudocode(f.read())

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
