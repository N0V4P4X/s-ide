# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5
"""
translator/ir.py
================
Structured IR for cross-language translation.
Extracted from S-IDE project graph + pseudocode generator.

Usage::
    from translator.ir import extract_project_ir
    ir = extract_project_ir(graph, project_root)
    for mod in ir.modules:
        for fn in mod.functions:
            print(fn.name, fn.is_pure, fn.pseudocode[:80])
"""
from __future__ import annotations
import ast, os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ArgSpec:
    name: str
    type_hint: str = ""
    default: str = ""
    kind: str = "positional"  # positional|keyword|var_positional|var_keyword


@dataclass
class FunctionIR:
    name: str
    args: list[ArgSpec]
    return_type: str
    pseudocode: str
    source: str
    calls: list[str]
    raises: list[str]
    complexity: int
    is_async: bool
    is_method: bool
    is_classmethod: bool
    is_staticmethod: bool
    is_property: bool
    is_pure: bool
    line_start: int
    line_end: int
    parent_class: str = ""

    def signature_str(self) -> str:
        args = ", ".join(
            f"{a.name}: {a.type_hint}" if a.type_hint else a.name
            for a in self.args
        )
        ret = f" -> {self.return_type}" if self.return_type else ""
        prefix = "async " if self.is_async else ""
        return f"{prefix}def {self.name}({args}){ret}"


@dataclass
class ClassIR:
    name: str
    bases: list[str]
    pseudocode: str
    methods: list[FunctionIR]
    class_vars: list[ArgSpec]
    is_dataclass: bool
    line_start: int
    line_end: int


@dataclass
class ImportIR:
    source: str
    names: list[str]
    alias: str = ""
    is_external: bool = False


@dataclass
class ModuleIR:
    path: str
    category: str
    imports: list[ImportIR]
    functions: list[FunctionIR]
    classes: list[ClassIR]
    module_docstring: str = ""
    lines: int = 0

    def all_functions(self) -> list[FunctionIR]:
        fns = list(self.functions)
        for cls in self.classes:
            fns.extend(cls.methods)
        return fns


@dataclass
class ProjectIR:
    root: str
    modules: list[ModuleIR]
    topo_order: list[str] = field(default_factory=list)

    def module(self, path: str) -> Optional[ModuleIR]:
        return next((m for m in self.modules if m.path == path), None)

    def function(self, name: str, path: str = "") -> Optional[FunctionIR]:
        for m in self.modules:
            if path and m.path != path:
                continue
            for fn in m.all_functions():
                if fn.name == name:
                    return fn
        return None


# ── Purity heuristic ─────────────────────────────────────────────────────────
_IMPURE = {
    "print","open","write","read","input","exit","quit",
}
_IMPURE_PREFIXES = ("os.","sys.","random.","time.","subprocess.","requests.","socket.")

def _is_pure(node: ast.FunctionDef, calls: list[str]) -> bool:
    for c in calls:
        if c in _IMPURE or any(c.startswith(p) for p in _IMPURE_PREFIXES):
            return False
    for n in ast.walk(node):
        if isinstance(n, ast.Global): return False
    return True


# ── Arg extraction ────────────────────────────────────────────────────────────
def _args(node: ast.arguments) -> list[ArgSpec]:
    specs = []
    pad   = len(node.args) - len(node.defaults)
    defs  = [None]*pad + node.defaults
    for arg, default in zip(node.args, defs):
        specs.append(ArgSpec(
            name=arg.arg,
            type_hint=ast.unparse(arg.annotation) if arg.annotation else "",
            default=ast.unparse(default) if default else "",
        ))
    if node.vararg:
        specs.append(ArgSpec(name=node.vararg.arg, kind="var_positional",
                             type_hint=ast.unparse(node.vararg.annotation) if node.vararg.annotation else ""))
    for arg in node.kwonlyargs:
        specs.append(ArgSpec(name=arg.arg, kind="keyword",
                             type_hint=ast.unparse(arg.annotation) if arg.annotation else ""))
    if node.kwarg:
        specs.append(ArgSpec(name=node.kwarg.arg, kind="var_keyword",
                             type_hint=ast.unparse(node.kwarg.annotation) if node.kwarg.annotation else ""))
    return specs


# ── Function IR ───────────────────────────────────────────────────────────────
def _fn_ir(node, source_lines: list[str], parent_class: str = "") -> FunctionIR:
    from parser.pseudocode import PseudocodeGenerator
    gen = PseudocodeGenerator()
    gen.visit(ast.Module(body=[node], type_ignores=[]))
    pseudocode = "\n".join(gen.output)

    decs = [ast.unparse(d) for d in node.decorator_list]

    calls, raises = [], []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name): calls.append(n.func.id)
            elif isinstance(n.func, ast.Attribute): calls.append(f"{ast.unparse(n.func.value)}.{n.func.attr}")
        if isinstance(n, ast.Raise) and n.exc:
            if isinstance(n.exc, ast.Call) and isinstance(n.exc.func, ast.Name): raises.append(n.exc.func.id)
            elif isinstance(n.exc, ast.Name): raises.append(n.exc.id)

    cx = 1
    for n in ast.walk(node):
        if isinstance(n, (ast.If, ast.While, ast.For, ast.ExceptHandler, ast.With, ast.Assert, ast.comprehension)):
            cx += 1

    fargs    = _args(node.args)
    is_meth  = bool(parent_class) and bool(fargs) and fargs[0].name in ("self", "cls")
    end_line = getattr(node, "end_lineno", node.lineno)
    src      = "\n".join(source_lines[node.lineno-1:end_line])

    return FunctionIR(
        name=node.name, args=fargs,
        return_type=ast.unparse(node.returns) if node.returns else "",
        pseudocode=pseudocode, source=src,
        calls=list(dict.fromkeys(calls)), raises=raises,
        complexity=cx,
        is_async=isinstance(node, ast.AsyncFunctionDef),
        is_method=is_meth,
        is_classmethod="classmethod" in decs,
        is_staticmethod="staticmethod" in decs,
        is_property="property" in decs,
        is_pure=_is_pure(node, calls) and not is_meth,
        line_start=node.lineno, line_end=end_line,
        parent_class=parent_class,
    )


# ── Class IR ──────────────────────────────────────────────────────────────────
def _class_ir(node: ast.ClassDef, source_lines: list[str]) -> ClassIR:
    from parser.pseudocode import PseudocodeGenerator
    gen = PseudocodeGenerator()
    gen.visit(ast.Module(body=[node], type_ignores=[]))
    decs    = [ast.unparse(d) for d in node.decorator_list]
    methods, class_vars = [], []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(_fn_ir(item, source_lines, parent_class=node.name))
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            class_vars.append(ArgSpec(
                name=item.target.id,
                type_hint=ast.unparse(item.annotation) if item.annotation else "",
                default=ast.unparse(item.value) if item.value else "",
            ))
    return ClassIR(
        name=node.name, bases=[ast.unparse(b) for b in node.bases],
        pseudocode="\n".join(gen.output),
        methods=methods, class_vars=class_vars,
        is_dataclass=any("dataclass" in d for d in decs),
        line_start=node.lineno,
        line_end=getattr(node, "end_lineno", node.lineno),
    )


# ── Module IR ─────────────────────────────────────────────────────────────────
def extract_module_ir(path: str, project_root: str) -> Optional[ModuleIR]:
    full = os.path.join(project_root, path)
    if not os.path.isfile(full): return None
    try:
        source = open(full, encoding="utf-8", errors="replace").read()
        tree   = ast.parse(source)
    except Exception: return None

    lines = source.splitlines()
    docstring = ""
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        docstring = tree.body[0].value.value

    imports: list[ImportIR] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                imports.append(ImportIR(source=a.name, names=[], alias=a.asname or ""))
        elif isinstance(n, ast.ImportFrom):
            imports.append(ImportIR(
                source=n.module or "",
                names=[a.name for a in n.names],
                alias=(n.names[0].asname or "") if len(n.names)==1 else "",
            ))

    functions, classes = [], []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_fn_ir(n, lines))
        elif isinstance(n, ast.ClassDef):
            classes.append(_class_ir(n, lines))

    return ModuleIR(
        path=os.path.relpath(full, project_root),
        category="python", imports=imports,
        functions=functions, classes=classes,
        module_docstring=docstring, lines=len(lines),
    )


# ── Project IR ────────────────────────────────────────────────────────────────
def extract_project_ir(graph: dict, project_root: str) -> ProjectIR:
    modules: list[ModuleIR] = []
    nodes = graph.get("nodes", [])
    internal_paths = {n["path"] for n in nodes if not n.get("isExternal")}

    for node in nodes:
        if node.get("category") != "python" or node.get("isExternal"): continue
        mod = extract_module_ir(node.get("path",""), project_root)
        if not mod: continue
        for imp in mod.imports:
            slug = imp.source.replace(".", "/")
            imp.is_external = not any(p.startswith(slug) or p.endswith(slug+".py")
                                      for p in internal_paths)
        modules.append(mod)

    topo = [n["path"] for n in nodes
            if n.get("category")=="python" and not n.get("isExternal")]
    return ProjectIR(root=project_root, modules=modules, topo_order=topo)
