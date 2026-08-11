# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
test/test_suite.py
==================
S-IDE core test suite. Uses only stdlib (unittest) — no pytest required,
though pytest will discover and run these fine.

Test groups
-----------
TestPythonParser    — AST-based python_parser
TestJSParser        — regex-based js_parser
TestJSONParser      — json_parser (package.json, tsconfig, generic)
TestShellParser     — shell_parser
TestWalker          — directory walker ignore logic
TestProjectConfig   — side.project.json load/save/init/bump
TestResolveEdges    — import → edge resolution
TestLayout          — auto-layout position assignment
TestDocCheck        — README / empty-module audit
TestProjectParser   — full parse pipeline on a synthetic project
TestProcessManager  — spawn, logs, stop
TestSideNodeAdapter — MythOS bridge handlers exercised directly (/api/nodes,
                      /api/infra, /api/xp) via a stub _json/_error sink
TestWebInfraIntegrity — web-infra.json edge/childId structural validation
"""

from __future__ import annotations
import json
import os
import sys
import tempfile
import time
import unittest

# ── Make sure parent dir is on sys.path so imports resolve ───────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── Imports under test ────────────────────────────────────────────────────────
from parser.parsers.python_parser import parse_python
from parser.parsers.js_parser import parse_javascript
from parser.parsers.json_parser import parse_json
from parser.parsers.shell_parser import parse_shell
from parser.walker import walk_directory, make_node_id, _should_ignore
from parser.project_config import load_project_config, save_project_config, init_project_config, bump_version
from parser.resolve_edges import resolve_edges, collect_external_packages
from parser.layout import assign_positions
from parser.doc_check import audit_docs
from parser.project_parser import parse_project
from process.process_manager import ProcessManager
from graph.types import FileNode, Edge, Position, ImportRecord


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tmp_project(*files: tuple[str, str]) -> tempfile.TemporaryDirectory:
    """
    Create a temporary directory pre-populated with (relative_path, content) files.
    Use as a context manager: `with _tmp_project(...) as tmp_dir: ...`
    The context variable is the directory path string.
    """
    tmp = tempfile.TemporaryDirectory()
    for rel_path, file_content in files:
        full = os.path.join(tmp.name, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(file_content)
    return tmp


# ═══════════════════════════════════════════════════════════════════════════════
# Parser tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPythonParser(unittest.TestCase):

    def test_basic_import(self):
        src = "import os\nimport sys\n"
        r = parse_python(src)
        sources = [i.source for i in r["imports"]]
        self.assertIn("os", sources)
        self.assertIn("sys", sources)

    def test_from_import(self):
        src = "from os.path import join, exists\n"
        r = parse_python(src)
        self.assertEqual(len(r["imports"]), 1)
        imp = r["imports"][0]
        self.assertEqual(imp.type, "from-import")
        self.assertEqual(imp.source, "os.path")
        self.assertIn("join", imp.names)

    def test_relative_import(self):
        src = "from . import utils\nfrom ..core import Base\n"
        r = parse_python(src)
        sources = [i.source for i in r["imports"]]
        self.assertIn(".", sources)
        self.assertIn("..core", sources)

    def test_function_definition(self):
        src = "def hello(name):\n    pass\n"
        r = parse_python(src)
        defs = [d.name for d in r["definitions"]]
        self.assertIn("hello", defs)

    def test_async_function(self):
        src = "async def fetch():\n    pass\n"
        r = parse_python(src)
        d = r["definitions"][0]
        self.assertTrue(d.is_async)
        self.assertEqual(d.name, "fetch")

    def test_class_with_base(self):
        src = "class Dog(Animal):\n    pass\n"
        r = parse_python(src)
        cls = r["definitions"][0]
        self.assertEqual(cls.kind, "class")
        self.assertIn("Animal", cls.bases)

    def test_dunder_method(self):
        src = "class Foo:\n    def __init__(self):\n        pass\n"
        r = parse_python(src)
        dunder = next(d for d in r["definitions"] if d.name == "__init__")
        self.assertEqual(dunder.kind, "dunder")

    def test_all_export(self):
        src = '__all__ = ["foo", "bar"]\ndef foo(): pass\ndef bar(): pass\n'
        r = parse_python(src)
        all_exp = next((e for e in r["exports"] if e.type == "__all__"), None)
        self.assertIsNotNone(all_exp)
        self.assertIn("foo", all_exp.names)

    def test_implicit_exports(self):
        src = "def public(): pass\ndef _private(): pass\n"
        r = parse_python(src)
        names = [e.name for e in r["exports"] if e.type == "implicit"]
        self.assertIn("public", names)
        self.assertNotIn("_private", names)

    def test_entrypoint_tag(self):
        src = "if __name__ == '__main__':\n    main()\n"
        r = parse_python(src)
        self.assertIn("entrypoint", r["tags"])

    def test_syntax_error_fallback(self):
        src = "def broken(\n    pass\n"
        r = parse_python(src)
        self.assertTrue(len(r["errors"]) > 0)

    def test_flask_tag(self):
        src = "from flask import Flask\n"
        r = parse_python(src)
        self.assertIn("flask", r["tags"])

    def test_star_import(self):
        src = "from utils import *\n"
        r = parse_python(src)
        self.assertEqual(r["imports"][0].type, "from-import-all")


class TestJSParser(unittest.TestCase):

    def test_es_default_import(self):
        src = "import React from 'react';\n"
        r = parse_javascript(src)
        self.assertEqual(r["imports"][0].type, "es-default")
        self.assertEqual(r["imports"][0].source, "react")

    def test_es_named_import(self):
        src = "import { useState, useEffect } from 'react';\n"
        r = parse_javascript(src)
        imp = r["imports"][0]
        self.assertEqual(imp.type, "es-named")
        self.assertIn("useState", imp.names)

    def test_cjs_require(self):
        src = "const path = require('path');\n"
        r = parse_javascript(src)
        self.assertEqual(r["imports"][0].type, "cjs-require")

    def test_export_default(self):
        src = "export default MyComponent;\n"
        r = parse_javascript(src)
        self.assertEqual(r["exports"][0].type, "default")
        self.assertEqual(r["exports"][0].name, "MyComponent")

    def test_reexport(self):
        src = "export { foo, bar } from './utils';\n"
        r = parse_javascript(src)
        exp = r["exports"][0]
        self.assertEqual(exp.type, "re-export")
        self.assertEqual(exp.source, "./utils")

    def test_function_def(self):
        src = "function greet(name) { return name; }\n"
        r = parse_javascript(src)
        self.assertIn("greet", [d.name for d in r["definitions"]])

    def test_comment_stripping(self):
        src = "// import foo from 'not-real';\nimport bar from 'real';\n"
        r = parse_javascript(src)
        sources = [i.source for i in r["imports"]]
        self.assertNotIn("not-real", sources)
        self.assertIn("real", sources)

    def test_react_tag(self):
        src = "import React from 'react';\n"
        r = parse_javascript(src)
        self.assertIn("react", r["tags"])


class TestJSONParser(unittest.TestCase):

    def test_package_json(self):
        src = json.dumps({
            "name": "my-app",
            "dependencies": {"express": "^4.0.0"},
            "scripts": {"start": "node index.js"},
        })
        r = parse_json(src, "package.json")
        self.assertIn("package-manifest", r["tags"])
        srcs = [i.source for i in r["imports"]]
        self.assertIn("express", srcs)
        script_names = [d.name for d in r["definitions"]]
        self.assertIn("start", script_names)

    def test_malformed_json(self):
        r = parse_json("{not valid}", "config.json")
        self.assertTrue(len(r["errors"]) > 0)

    def test_tsconfig(self):
        src = json.dumps({"compilerOptions": {"paths": {"@utils/*": ["src/utils/*"]}}})
        r = parse_json(src, "tsconfig.json")
        self.assertIn("typescript-config", r["tags"])


class TestShellParser(unittest.TestCase):

    def test_source_import(self):
        src = "source ./lib/helpers.sh\n"
        r = parse_shell(src)
        self.assertEqual(r["imports"][0].type, "source")
        self.assertIn("./lib/helpers.sh", r["imports"][0].source)

    def test_env_var_export(self):
        src = "export DATABASE_URL=postgres://localhost\n"
        r = parse_shell(src)
        names = [e.name for e in r["exports"]]
        self.assertIn("DATABASE_URL", names)

    def test_function_def(self):
        src = "function setup() {\n  echo hi\n}\n"
        r = parse_shell(src)
        self.assertIn("setup", [d.name for d in r["definitions"]])

    def test_docker_tag(self):
        src = "docker build -t myimage .\n"
        r = parse_shell(src)
        self.assertIn("docker", r["tags"])


# ═══════════════════════════════════════════════════════════════════════════════
# Walker tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestWalker(unittest.TestCase):

    def test_basic_walk(self):
        with _tmp_project(
            ("main.py", "# main"),
            ("utils/helpers.py", "# helpers"),
        ) as tmp:
            files = walk_directory(tmp)
            rel_paths = [f.relative_path for f in files]
            self.assertIn("main.py", rel_paths)
            self.assertIn("utils/helpers.py", rel_paths)

    def test_node_modules_ignored(self):
        with _tmp_project(
            ("index.js", ""),
            ("node_modules/express/index.js", ""),
        ) as tmp:
            files = walk_directory(tmp)
            paths = [f.relative_path for f in files]
            self.assertIn("index.js", paths)
            self.assertFalse(any("node_modules" in p for p in paths))

    def test_pycache_ignored(self):
        with _tmp_project(
            ("app.py", ""),
            ("__pycache__/app.cpython-311.pyc", ""),
        ) as tmp:
            files = walk_directory(tmp)
            paths = [f.relative_path for f in files]
            self.assertFalse(any("__pycache__" in p for p in paths))

    def test_extra_ignore(self):
        with _tmp_project(
            ("src/main.py", ""),
            ("dist/bundle.js", ""),
        ) as tmp:
            files = walk_directory(tmp, extra_ignore=["dist"])
            paths = [f.relative_path for f in files]
            self.assertFalse(any("dist" in p for p in paths))

    def test_make_node_id(self):
        self.assertEqual(make_node_id("src/utils/helpers.py"), "src_utils_helpers_py")
        self.assertEqual(make_node_id("main.py"), "main_py")

    def test_venv_ignored(self):
        with _tmp_project(
            ("app.py", ""),
            (".venv/lib/python3.11/site-packages/x.py", ""),
            ("venv/lib/x.py", ""),
            ("env/lib/x.py", ""),
        ) as tmp:
            files = walk_directory(tmp)
            paths = [f.relative_path for f in files]
            self.assertIn("app.py", paths)
            self.assertTrue(all("venv" not in p and "env/" not in p for p in paths))

    def test_git_dir_ignored(self):
        with _tmp_project(
            ("app.py", ""),
            (".git/objects/aa/bb", ""),
        ) as tmp:
            files = walk_directory(tmp)
            paths = [f.relative_path for f in files]
            self.assertIn("app.py", paths)
            self.assertFalse(any(".git" in p for p in paths))

    def test_hidden_files_ignored(self):
        with _tmp_project(
            ("visible.py", ""),
            (".hidden.py", ""),
        ) as tmp:
            files = walk_directory(tmp)
            paths = [f.relative_path for f in files]
            self.assertIn("visible.py", paths)
            self.assertNotIn(".hidden.py", paths)


# ═══════════════════════════════════════════════════════════════════════════════
# Project config tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestProjectConfig(unittest.TestCase):

    def test_bump_patch(self):
        self.assertEqual(bump_version("1.2.3", "patch"), "1.2.4")

    def test_bump_minor(self):
        self.assertEqual(bump_version("1.2.3", "minor"), "1.3.0")

    def test_bump_major(self):
        self.assertEqual(bump_version("1.2.3", "major"), "2.0.0")

    def test_bump_defaults_patch(self):
        self.assertEqual(bump_version("0.0.1"), "0.0.2")

    def test_init_creates_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = init_project_config(tmp)
            self.assertTrue(os.path.exists(os.path.join(tmp, "side.project.json")))
            self.assertEqual(config["name"], os.path.basename(tmp))

    def test_load_missing_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_project_config(tmp)
            self.assertFalse(config["_exists"])
            self.assertIn("versions", config)

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = init_project_config(tmp)
            config["version"] = "9.9.9"
            save_project_config(tmp, config)
            reloaded = load_project_config(tmp)
            self.assertEqual(reloaded["version"], "9.9.9")

    def test_no_internal_keys_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = init_project_config(tmp)
            config_path = os.path.join(tmp, "side.project.json")
            with open(config_path) as f:
                raw = json.load(f)
            for key in raw:
                self.assertFalse(key.startswith("_"), f"Internal key found: {key}")


# ═══════════════════════════════════════════════════════════════════════════════
# Edge resolver tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestResolveEdges(unittest.TestCase):

    def _make_node(self, rel_path: str, imports=None) -> FileNode:
        return FileNode(
            id=make_node_id(rel_path),
            label=os.path.basename(rel_path),
            path=rel_path,
            full_path=f"/proj/{rel_path}",
            category="python",
            ext=".py",
            imports=imports or [],
        )

    def test_resolves_relative_import(self):
        main = self._make_node("main.py", [
            ImportRecord(type="from-import", source="utils", names=["helper"])
        ])
        utils = self._make_node("utils.py")
        file_index = {"main.py": main.id, "utils.py": utils.id}
        edges = resolve_edges([main, utils], file_index, "/proj")
        self.assertTrue(any(e.source == main.id and e.target == utils.id for e in edges))

    def test_external_package(self):
        node = self._make_node("app.py", [
            ImportRecord(type="import", source="requests")
        ])
        file_index = {"app.py": node.id}
        edges = resolve_edges([node], file_index, "/proj")
        ext_edges = [e for e in edges if e.is_external]
        self.assertTrue(len(ext_edges) > 0)
        self.assertEqual(ext_edges[0].external_pkg, "requests")

    def test_no_duplicate_edges(self):
        main = self._make_node("main.py", [
            ImportRecord(type="from-import", source="utils", names=["a"]),
            ImportRecord(type="from-import", source="utils", names=["b"]),
        ])
        utils = self._make_node("utils.py")
        file_index = {"main.py": main.id, "utils.py": utils.id}
        edges = resolve_edges([main, utils], file_index, "/proj")
        internal = [e for e in edges if not e.is_external]
        self.assertEqual(len(internal), 1)

    def test_collect_external_packages(self):
        node = self._make_node("a.py", [ImportRecord(type="import", source="numpy")])
        file_index = {"a.py": node.id}
        edges = resolve_edges([node], file_index, "/proj")
        pkgs = collect_external_packages(edges)
        self.assertEqual(pkgs[0]["name"], "numpy")


# ═══════════════════════════════════════════════════════════════════════════════
# Layout tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestLayout(unittest.TestCase):

    def _node(self, nid: str) -> FileNode:
        return FileNode(id=nid, label=nid, path=f"{nid}.py", full_path=f"/p/{nid}.py",
                        category="python", ext=".py")

    def test_all_nodes_get_positions(self):
        nodes = [self._node("a"), self._node("b"), self._node("c")]
        edges = [Edge(id="e0", source="a", target="b", type="import")]
        assign_positions(nodes, edges)
        for n in nodes:
            self.assertIsNotNone(n.position, f"{n.id} has no position")

    def test_root_at_zero(self):
        # New clustered layout adds CLUSTER_PAD offset within the cluster bounding box.
        # The important invariant is that root comes before child (lower x).
        nodes = [self._node("root"), self._node("child")]
        edges = [Edge(id="e0", source="root", target="child", type="import")]
        assign_positions(nodes, edges)
        root = next(n for n in nodes if n.id == "root")
        child = next(n for n in nodes if n.id == "child")
        self.assertIsNotNone(root.position)
        self.assertGreater(child.position.x, root.position.x)

    def test_empty_graph(self):
        assign_positions([], [])   # should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# Doc check tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDocCheck(unittest.TestCase):

    def _node(self, path: str, **kwargs) -> FileNode:
        return FileNode(
            id=make_node_id(path), label=os.path.basename(path),
            path=path, full_path=f"/p/{path}",
            category=kwargs.get("category", "python"), ext=kwargs.get("ext", ".py"),
            imports=kwargs.get("imports", []),
            exports=kwargs.get("exports", []),
            definitions=kwargs.get("definitions", []),
        )

    def test_missing_readme_warning(self):
        nodes = [self._node("src/main.py")]
        audit = audit_docs("/p", nodes)
        types = [w.type for w in audit.warnings]
        self.assertIn("missing-readme", types)

    def test_healthy_with_readme(self):
        from graph.types import ExportRecord, Definition
        src_node = self._node("src/main.py",
                               exports=[ExportRecord(type="implicit", name="main", kind="function")],
                               definitions=[Definition(name="main", kind="function", line=1)])
        readme = self._node("src/README.md", category="docs", ext=".md")
        audit = audit_docs("/p", [src_node, readme])
        missing = [w for w in audit.warnings if w.type == "missing-readme"]
        self.assertEqual(len(missing), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Full pipeline test
# ═══════════════════════════════════════════════════════════════════════════════

class TestProjectParser(unittest.TestCase):

    def test_parse_synthetic_project(self):
        with _tmp_project(
            ("main.py", "from utils.helpers import greet\n\nif __name__ == '__main__':\n    greet('world')\n"),
            ("utils/__init__.py", ""),
            ("utils/helpers.py", "def greet(name):\n    print(f'Hello, {name}')\n"),
            ("README.md", "# Test project\n"),
        ) as tmp:
            graph = parse_project(tmp)
            d = graph.to_dict()

            self.assertGreaterEqual(d["meta"]["totalFiles"], 3)
            node_paths = [n["path"] for n in d["nodes"]]
            self.assertIn("main.py", node_paths)
            self.assertIn("utils/helpers.py", node_paths)

            # main.py should have an edge to utils/helpers.py
            edges = d["edges"]
            main_id = make_node_id("main.py")
            helpers_id = make_node_id("utils/helpers.py")
            internal = [e for e in edges if not e.get("isExternal")]
            found = any(e["source"] == main_id and e["target"] == helpers_id for e in internal)
            self.assertTrue(found, "Expected edge main.py → utils/helpers.py")

    def test_parse_returns_positions(self):
        with _tmp_project(("app.py", "import os\n")) as tmp:
            graph = parse_project(tmp)
            for node in graph.nodes:
                self.assertIsNotNone(node.position, f"{node.path} missing position")

    def test_graph_serialises_to_json(self):
        with _tmp_project(("hello.py", "print('hi')\n")) as tmp:
            graph = parse_project(tmp)
            # Should not raise
            txt = json.dumps(graph.to_dict())
            self.assertIn("nodes", txt)

    def test_meta_perf_recorded(self):
        """The inlined ParseTimer (ex-monitor.perf) surfaces per-stage timing
        under meta.perf for every parse."""
        with _tmp_project(("app.py", "import os\n")) as tmp:
            graph = parse_project(tmp)
            perf = graph.to_dict()["meta"].get("perf", {})
            self.assertIn("total_ms", perf)
            self.assertGreater(perf["total_ms"], 0)
            names = [s["name"] for s in perf.get("stages", [])]
            self.assertIn("walk", names)
            self.assertIn("parse_files", names)
            self.assertIn("resolve_edges", names)
            self.assertIn("write_json", names)


# ═══════════════════════════════════════════════════════════════════════════════
# Process manager tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestProcessManager(unittest.TestCase):

    def test_start_and_exit(self):
        mgr = ProcessManager()
        proc = mgr.start(name="echo", command="echo hello")
        time.sleep(0.5)
        info = proc.info()
        self.assertIn(info["status"], ("stopped", "crashed", "running"))

    def test_logs_captured(self):
        mgr = ProcessManager()
        proc = mgr.start(name="echo", command="echo captured_line")
        time.sleep(0.5)
        logs = proc.logs()
        lines = [entry["line"] for entry in logs]
        self.assertTrue(any("captured_line" in l for l in lines))

    def test_stop(self):
        mgr = ProcessManager()
        # Use a long-running process
        proc = mgr.start(name="sleep", command="sleep 30")
        time.sleep(0.2)
        ok = mgr.stop(proc.id)
        self.assertTrue(ok)

    def test_list(self):
        mgr = ProcessManager()
        mgr.start(name="p1", command="echo a")
        mgr.start(name="p2", command="echo b")
        time.sleep(0.3)
        listing = mgr.list()
        self.assertEqual(len(listing), 2)

    def test_on_stdout_callback(self):
        received = []
        mgr = ProcessManager()
        proc = mgr.start(name="cb-test", command="echo callback_works")
        proc.on_stdout(received.append)
        time.sleep(0.5)
        # Callback may fire after attachment; check logs as fallback
        logs = [e["line"] for e in proc.logs()]
        self.assertTrue(
            any("callback_works" in l for l in logs),
            "Expected output not found in logs"
        )

    def test_purge_stopped(self):
        mgr = ProcessManager()
        mgr.start(name="quick", command="echo done")
        time.sleep(0.5)
        removed = mgr.purge_stopped()
        self.assertGreaterEqual(removed, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# TOML / YAML parser tests
# ═══════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
# TOML / YAML parser tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTomlParser(unittest.TestCase):

    def test_pyproject_name_and_deps(self):
        from parser.parsers.toml_yaml_parser import parse_toml
        src = '[project]\nname = "myapp"\nversion = "1.0.0"\ndependencies = ["requests>=2.0", "flask"]\n'
        r = parse_toml(src, "pyproject.toml")
        self.assertIn("pyproject", r["tags"])
        self.assertTrue(any("pkg:myapp" in t for t in r["tags"]))
        sources = [i.source for i in r["imports"]]
        self.assertIn("requests", sources)
        self.assertIn("flask", sources)

    def test_cargo_toml_deps(self):
        from parser.parsers.toml_yaml_parser import parse_toml
        src = '[package]\nname = "mylib"\nversion = "0.1.0"\n\n[dependencies]\nserde = "1.0"\ntokio = { version = "1", features = ["full"] }\n'
        r = parse_toml(src, "Cargo.toml")
        self.assertIn("cargo", r["tags"])
        sources = [i.source for i in r["imports"]]
        self.assertIn("serde", sources)
        self.assertIn("tokio", sources)

    def test_generic_toml_keys(self):
        from parser.parsers.toml_yaml_parser import parse_toml
        src = '[database]\nhost = "localhost"\nport = 5432\n'
        r = parse_toml(src, "config.toml")
        names = [d.name for d in r["definitions"]]
        self.assertIn("database", names)

    def test_pyproject_tool_detection(self):
        from parser.parsers.toml_yaml_parser import parse_toml
        src = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n[tool.black]\nline-length = 88\n'
        r = parse_toml(src, "pyproject.toml")
        self.assertTrue(any("tool:pytest" in t for t in r["tags"]))
        self.assertTrue(any("tool:black" in t for t in r["tags"]))


class TestYamlParser(unittest.TestCase):

    def test_docker_compose_services(self):
        from parser.parsers.toml_yaml_parser import parse_yaml
        src = 'version: "3"\nservices:\n  web:\n    image: nginx:latest\n  db:\n    image: postgres:15\n'
        r = parse_yaml(src, "docker-compose.yml")
        self.assertIn("docker-compose", r["tags"])
        service_names = [d.name for d in r["definitions"]]
        self.assertIn("web", service_names)
        self.assertIn("db", service_names)
        images = [i.source for i in r["imports"]]
        self.assertIn("nginx", images)
        self.assertIn("postgres", images)

    def test_github_workflow_jobs(self):
        from parser.parsers.toml_yaml_parser import parse_yaml
        src = 'name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n  build:\n    runs-on: ubuntu-latest\n'
        r = parse_yaml(src, ".github/workflows/ci.yml")
        self.assertIn("github-actions", r["tags"])
        job_names = [d.name for d in r["definitions"]]
        self.assertIn("test", job_names)
        self.assertIn("build", job_names)

    def test_generic_yaml_keys(self):
        from parser.parsers.toml_yaml_parser import parse_yaml
        src = 'server:\n  host: localhost\n  port: 8080\ndatabase:\n  url: postgres://\n'
        r = parse_yaml(src, "config.yaml")
        names = [d.name for d in r["definitions"]]
        self.assertIn("server", names)
        self.assertIn("database", names)

    def test_toml_in_parsers_dispatch(self):
        from parser.parsers import PARSERS
        self.assertIn(".toml", PARSERS)
        self.assertIn(".yaml", PARSERS)
        self.assertIn(".yml", PARSERS)

    def test_pyproject_parsed_in_full_pipeline(self):
        """pyproject.toml dependencies appear as external edges in the graph."""
        import json
        from parser.project_parser import parse_project
        pyproject_src = '[project]\nname = "testpkg"\nversion = "0.1.0"\ndependencies = ["requests"]\n'
        with _tmp_project(
            ("pyproject.toml", pyproject_src),
            ("main.py", "import requests\n"),
        ) as tmp:
            graph = parse_project(tmp, save_json=False)
            # pyproject.toml should have a node
            paths = [n.path for n in graph.nodes]
            self.assertIn("pyproject.toml", paths)
            # requests dep node from pyproject.toml
            pyproject_node = next(n for n in graph.nodes if n.path == "pyproject.toml")
            dep_sources = [i.source for i in pyproject_node.imports]
            self.assertIn("requests", dep_sources)

# ═══════════════════════════════════════════════════════════════════════════════
# Python parser data-flow tests (new single-pass parser)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPythonParserDataFlow(unittest.TestCase):

    def test_function_args_extracted(self):
        from parser.parsers.python_parser import parse_python
        src = "def greet(name: str, count: int = 1) -> str:\n    return name\n"
        r = parse_python(src)
        fn = next(d for d in r["definitions"] if d.name == "greet")
        arg_names = [a[0] for a in fn.args]
        self.assertIn("name", arg_names)
        self.assertIn("count", arg_names)

    def test_return_type_extracted(self):
        from parser.parsers.python_parser import parse_python
        src = "def fetch() -> dict:\n    return {}\n"
        r = parse_python(src)
        fn = r["definitions"][0]
        self.assertEqual(fn.return_type, "dict")

    def test_calls_extracted(self):
        from parser.parsers.python_parser import parse_python
        src = (
            "def run():\n"
            "    result = process(data)\n"
            "    log.info('done')\n"
            "    return result\n"
        )
        r = parse_python(src)
        fn = r["definitions"][0]
        self.assertTrue(any("process" in c for c in fn.calls))

    def test_raises_extracted(self):
        from parser.parsers.python_parser import parse_python
        src = (
            "def load(path):\n"
            "    if not path:\n"
            "        raise ValueError('empty path')\n"
            "    return open(path).read()\n"
        )
        r = parse_python(src)
        fn = r["definitions"][0]
        self.assertIn("ValueError", fn.raises)

    def test_complexity_simple(self):
        from parser.parsers.python_parser import parse_python
        src = "def simple():\n    return 1\n"
        r = parse_python(src)
        self.assertEqual(r["definitions"][0].complexity, 1)

    def test_complexity_branching(self):
        from parser.parsers.python_parser import parse_python
        src = (
            "def classify(x):\n"
            "    if x > 0:\n"
            "        return 'pos'\n"
            "    elif x < 0:\n"
            "        return 'neg'\n"
            "    else:\n"
            "        return 'zero'\n"
        )
        r = parse_python(src)
        fn = r["definitions"][0]
        self.assertGreater(fn.complexity, 1)

    def test_end_line_populated(self):
        from parser.parsers.python_parser import parse_python
        src = "def foo():\n    x = 1\n    return x\n"
        r = parse_python(src)
        fn = r["definitions"][0]
        self.assertIsNotNone(fn.end_line)
        self.assertGreater(fn.end_line, fn.line)

    def test_class_bases_and_defs(self):
        from parser.parsers.python_parser import parse_python
        src = "class Dog(Animal, Runnable):\n    def bark(self):\n        pass\n"
        r = parse_python(src)
        cls = next(d for d in r["definitions"] if d.kind == "class")
        self.assertIn("Animal", cls.bases)
        self.assertIn("Runnable", cls.bases)
        methods = [d for d in r["definitions"] if d.kind == "method"]
        self.assertTrue(any(m.name == "bark" for m in methods))

    def test_async_flag(self):
        from parser.parsers.python_parser import parse_python
        src = "async def fetch(url: str):\n    pass\n"
        r = parse_python(src)
        fn = r["definitions"][0]
        self.assertTrue(fn.is_async)
        arg_names = [a[0] for a in fn.args]
        self.assertIn("url", arg_names)

    def test_decorator_captured(self):
        from parser.parsers.python_parser import parse_python
        src = "@timed\ndef work():\n    pass\n"
        r = parse_python(src)
        fn = r["definitions"][0]
        self.assertTrue(any("timed" in d for d in fn.decorators))


# ═══════════════════════════════════════════════════════════════════════════════
# Filter logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestFilterLogic(unittest.TestCase):

    def _nodes(self):
        return [
            {"id": "a", "category": "python",     "isExternal": False},
            {"id": "b", "category": "javascript", "isExternal": False},
            {"id": "c", "category": "docs",       "isExternal": False},
            {"id": "d", "category": "config",     "isExternal": False},
            {"id": "e", "category": "python",     "isExternal": True},
        ]

    def _vis(self, nodes, filter_cats, hidden_cats, show_ext=False):
        return [n for n in nodes
                if (show_ext or not n.get("isExternal"))
                and (n.get("category") not in hidden_cats
                     or n.get("category") in filter_cats)
                and (not filter_cats or n.get("category") in filter_cats)]

    def test_docs_hidden_by_default(self):
        v = self._vis(self._nodes(), set(), {"docs", "config"})
        self.assertFalse(any(n["category"] == "docs" for n in v))

    def test_python_visible_by_default(self):
        v = self._vis(self._nodes(), set(), {"docs", "config"})
        self.assertTrue(any(n["category"] == "python" for n in v))

    def test_ext_hidden_by_default(self):
        v = self._vis(self._nodes(), set(), {"docs", "config"}, show_ext=False)
        self.assertFalse(any(n["isExternal"] for n in v))

    def test_selecting_docs_shows_them(self):
        v = self._vis(self._nodes(), {"docs"}, set())
        self.assertTrue(any(n["category"] == "docs" for n in v))

    def test_multi_select_py_js(self):
        v = self._vis(self._nodes(), {"python", "javascript"}, {"docs", "config"})
        cats = {n["category"] for n in v}
        self.assertEqual(cats, {"python", "javascript"})

    def test_all_clears(self):
        v = self._vis(self._nodes(), set(), {"docs", "config"})
        cats = {n["category"] for n in v}
        self.assertIn("python", cats)
        self.assertNotIn("docs", cats)


# ═══════════════════════════════════════════════════════════════════════════════
# Doc links — directory matching
# ═══════════════════════════════════════════════════════════════════════════════

class TestDocLinks(unittest.TestCase):

    def _same(self, a, b):
        return os.path.dirname(a) == os.path.dirname(b)

    def test_readme_links_sibling(self):
        self.assertTrue(self._same("gui/README.md", "gui/app.py"))

    def test_readme_not_subdir(self):
        self.assertFalse(self._same("README.md", "gui/app.py"))

    def test_root_readme_root_file(self):
        self.assertTrue(self._same("README.md", "main.py"))

    def test_nested_readme(self):
        self.assertTrue(self._same("parser/README.md", "parser/walker.py"))

    def test_cross_dir_no_link(self):
        self.assertFalse(self._same("parser/README.md", "gui/app.py"))


# ═══════════════════════════════════════════════════════════════════════════════
# Filter logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalculatorExample(unittest.TestCase):
    '''Integration tests for examples/calculator — runs its own test suite.'''

    CALC_DIR = os.path.join(os.path.dirname(__file__), '..', 'examples', 'calculator')

    def test_example_project_exists(self):
        self.assertTrue(os.path.isdir(self.CALC_DIR))

    def test_example_has_side_project_json(self):
        self.assertTrue(os.path.isfile(os.path.join(self.CALC_DIR, 'side.project.json')))

    def test_example_has_readme(self):
        self.assertTrue(os.path.isfile(os.path.join(self.CALC_DIR, 'README.md')))

    def test_pemdas_module_importable(self):
        import sys
        sys.path.insert(0, os.path.join(self.CALC_DIR))
        try:
            from src.pemdas import evaluate, ParseError
            self.assertAlmostEqual(evaluate('3 + 4 * 2'), 11)
        finally:
            sys.path.pop(0)

    def test_pemdas_right_assoc_exp(self):
        import sys
        sys.path.insert(0, os.path.join(self.CALC_DIR))
        try:
            from src.pemdas import evaluate
            self.assertAlmostEqual(evaluate('2 ** 3 ** 2'), 512)
        finally:
            sys.path.pop(0)

    def test_pemdas_parens(self):
        import sys
        sys.path.insert(0, os.path.join(self.CALC_DIR))
        try:
            from src.pemdas import evaluate
            self.assertAlmostEqual(evaluate('(3 + 4) * 2'), 14)
        finally:
            sys.path.pop(0)

    def test_calculator_suite_passes(self):
        '''Run the calculator's own test suite as a subprocess.'''
        import subprocess
        result = subprocess.run(
            [sys.executable, '-m', 'unittest', 'discover', 'test/', '-v'],
            cwd=self.CALC_DIR, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
            msg=f'Calculator tests failed:\n{result.stderr}')


# ══════════════════════════════════════
# Workspace manifest
# ══════════════════════════════════════

class TestWorkspaceManifest(unittest.TestCase):

    def test_init_workspace(self):
        from parser.workspace import init_workspace, WORKSPACE_FILE
        with tempfile.TemporaryDirectory() as root:
            m = init_workspace(root, 'test')
            self.assertEqual(m.name, 'test')
            self.assertTrue(os.path.isfile(os.path.join(root, WORKSPACE_FILE)))

    def test_init_finds_projects(self):
        from parser.workspace import init_workspace
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, 'proj-a'))
            with open(os.path.join(root, 'proj-a', 'side.project.json'), 'w') as f:
                f.write('{}')
            m = init_workspace(root)
            self.assertIn('proj-a', m.projects)

    def test_save_and_load(self):
        from parser.workspace import save_workspace, load_workspace
        with tempfile.TemporaryDirectory() as root:
            from parser.workspace import WorkspaceManifest
            m = WorkspaceManifest(name='ws', packages={'numpy': '>=1.24'})
            save_workspace(root, m)
            m2 = load_workspace(root)
            self.assertEqual(m2.name, 'ws')
            self.assertEqual(m2.packages['numpy'], '>=1.24')

    def test_load_missing_returns_empty(self):
        from parser.workspace import load_workspace
        with tempfile.TemporaryDirectory() as root:
            m = load_workspace(root)
            self.assertIsInstance(m.packages, dict)

    def test_find_workspace_root(self):
        from parser.workspace import init_workspace, find_workspace_root
        with tempfile.TemporaryDirectory() as root:
            init_workspace(root)
            proj = os.path.join(root, 'myproject')
            os.makedirs(proj)
            found = find_workspace_root(proj)
            self.assertEqual(os.path.abspath(found), os.path.abspath(root))

    def test_find_workspace_root_not_found(self):
        from parser.workspace import find_workspace_root
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_workspace_root(tmp))

    def test_resolve_deps_from_imports(self):
        from parser.workspace import resolve_project_deps, WorkspaceManifest
        with tempfile.TemporaryDirectory() as proj:
            with open(os.path.join(proj, 'main.py'), 'w') as f:
                f.write('import requests\nfrom numpy import array\nimport os\n')
            m = WorkspaceManifest(packages={'requests':'>=2','numpy':'*','flask':'*'})
            deps = resolve_project_deps(proj, m)
            self.assertIn('requests', deps)
            self.assertIn('numpy', deps)
            self.assertNotIn('flask', deps)  # not imported
            self.assertNotIn('os', deps)      # stdlib, not in manifest

    def test_requirements_txt(self):
        from parser.workspace import WorkspaceManifest
        m = WorkspaceManifest(packages={'requests':'>=2.28','numpy':'*'})
        txt = m.requirements_txt()
        self.assertIn('requests>=2.28', txt)
        self.assertIn('numpy', txt)

    def test_add_package(self):
        from parser.workspace import WorkspaceManifest
        m = WorkspaceManifest()
        m.add_package('flask', '>=2.3')
        self.assertEqual(m.packages['flask'], '>=2.3')

    def test_remove_package(self):
        from parser.workspace import WorkspaceManifest
        m = WorkspaceManifest(packages={'flask': '>=2.3'})
        ok = m.remove_package('flask')
        self.assertTrue(ok)
        self.assertNotIn('flask', m.packages)
        self.assertFalse(m.remove_package('nonexistent'))

    def test_workspace_summary(self):
        from parser.workspace import init_workspace, workspace_summary, save_workspace
        with tempfile.TemporaryDirectory() as root:
            m = init_workspace(root, 'myws')
            m.add_package('rich', '*')
            save_workspace(root, m)
            s = workspace_summary(root)
            self.assertIn('myws', s)
            self.assertIn('rich', s)

    def test_find_projects_in_workspace(self):
        from parser.workspace import find_projects_in_workspace
        with tempfile.TemporaryDirectory() as root:
            for proj in ('proj-a', 'proj-b'):
                os.makedirs(os.path.join(root, proj))
                with open(os.path.join(root, proj, 'side.project.json'), 'w') as f:
                    json.dump({"name": proj}, f)
            os.makedirs(os.path.join(root, 'not-a-project'))   # no marker
            os.makedirs(os.path.join(root, '.hidden-project'))  # dotfile skipped
            found = find_projects_in_workspace(root)
            self.assertEqual(found, ['proj-a', 'proj-b'])

    def test_add_package_module_level(self):
        from parser.workspace import add_package, load_workspace
        with tempfile.TemporaryDirectory() as root:
            m = add_package(root, 'requests', '>=2.28')
            self.assertEqual(m.packages['requests'], '>=2.28')
            reloaded = load_workspace(root)
            self.assertEqual(reloaded.packages['requests'], '>=2.28')

    def test_resolve_project_deps_uses_graph_external_edges(self):
        """The graph fast path must match the real resolve_edges output shape
        (target ext_<pkg> + externalPackage), not the pre-rewrite ext: form."""
        from parser.workspace import resolve_project_deps, WorkspaceManifest
        with tempfile.TemporaryDirectory() as proj:
            with open(os.path.join(proj, 'main.py'), 'w') as f:
                f.write('import os\n')
            graph = {
                "nodes": [{"id": "main_py", "path": "main.py", "isExternal": False}],
                "edges": [
                    {"id": "e_0", "source": "main_py", "target": "ext_requests",
                     "type": "external", "isExternal": True, "externalPackage": "requests"},
                    {"id": "e_1", "source": "main_py", "target": "ext_numpy",
                     "type": "external", "isExternal": True, "externalPackage": "numpy"},
                ],
            }
            m = WorkspaceManifest(packages={'requests': '>=2', 'numpy': '*', 'flask': '*'})
            deps = resolve_project_deps(proj, m, graph)
            self.assertEqual(set(deps), {'requests', 'numpy'})
            self.assertNotIn('flask', deps)

    def test_collect_external_imports_scan(self):
        from parser.workspace import _collect_external_imports
        with tempfile.TemporaryDirectory() as proj:
            with open(os.path.join(proj, 'main.py'), 'w') as f:
                f.write('import requests\nfrom numpy import array\nimport os\n')
            imports = _collect_external_imports(proj, None)
            self.assertIn('requests', imports)
            self.assertIn('numpy', imports)
            self.assertIn('os', imports)


class TestSideNodeAdapter(unittest.TestCase):
    """Real-handler tests for the MythOS bridge (/api/nodes, /api/infra, /api/xp).

    These call the actual gui.server handlers rather than re-implementing the
    adapter logic, so the contract under test is the code MythOS consumes.
    A stub _json/_error captures status + body; no HTTP socket is involved.
    """

    @staticmethod
    def _handler():
        """Return (handler, responses) where responses is a list of
        {"status": int, "body": object} tuples captured by the stub sink."""
        from gui.server import Handler

        class _Stub(Handler):
            def __init__(self):
                pass

            def _json(self, data):
                responses.append({"status": 200, "body": data})

            def _error(self, code, msg):
                responses.append({"status": code, "body": {"error": str(msg)}})

        responses = []
        h = _Stub()
        return h, responses

    @staticmethod
    def _write_graph(root):
        """Write a hermetic .nodegraph.json into root; return the dict."""
        g = {
            "version": "1.0.0",
            "meta": {"root": root},
            "nodes": [
                {
                    "id": "app_py",
                    "label": "app.py",
                    "path": "app.py",
                    "fullPath": os.path.join(root, "app.py"),
                    "category": "source",
                    "ext": ".py",
                    "lines": 100,
                    "size": 0,
                    "modified": None,
                    "imports": [
                        {"type": "import", "source": "utils"},
                        {"type": "import", "source": "missing_lib"},
                    ],
                    "exports": [{"type": "function", "name": "run"}],
                    "definitions": [{"type": "function", "name": "main"}],
                    "tags": [],
                    "errors": [],
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "utils_py",
                    "label": "utils.py",
                    "path": "utils.py",
                    "fullPath": os.path.join(root, "utils.py"),
                    "category": "source",
                    "ext": ".py",
                    "lines": 10,
                    "size": 0,
                    "modified": None,
                    "imports": [],
                    "exports": [],
                    "definitions": [],
                    "tags": [],
                    "errors": [],
                    "position": {"x": 10, "y": 10},
                },
                {
                    "id": "readme_md",
                    "label": "README.md",
                    "path": "README.md",
                    "fullPath": os.path.join(root, "README.md"),
                    "category": "docs",
                    "ext": ".md",
                    "lines": 5,
                    "size": 0,
                    "modified": None,
                    "imports": [],
                    "exports": [],
                    "definitions": [],
                    "tags": [],
                    "errors": [],
                    "position": None,
                },
            ],
            "edges": [],
        }
        with open(os.path.join(root, ".nodegraph.json"), "w", encoding="utf-8") as f:
            json.dump(g, f)
        return g

    def test_get_nodes_returns_exact_side_node_shape(self):
        """Every node converts to the documented SideNode shape."""
        h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            self._write_graph(root)
            h._get_nodes({"root": [root]})

            self.assertEqual(len(responses), 1)
            self.assertEqual(responses[0]["status"], 200)
            nodes = {n["id"]: n for n in responses[0]["body"]}
            self.assertEqual(set(nodes), {"side:app_py", "side:utils_py", "side:readme_md"})

            app = nodes["side:app_py"]
            self.assertEqual(app["label"], "app.py")
            self.assertEqual(app["detail"], "File: app.py\n100 lines, 2 imports, 1 exports, 1 definitions")
            self.assertEqual(app["kind"], "task")
            self.assertEqual(app["category"], "source")
            self.assertEqual(app["skillHints"], ["python", "backend", "utils", "missing_lib"])
            self.assertEqual(app["estimateHours"], 2.0)
            self.assertEqual(app["childIds"], ["side:utils"])
            self.assertEqual(app["_source"], {
                "project": root,
                "path": "app.py",
                "category": "source",
                "ext": ".py",
                "position": {"x": 0, "y": 0},
            })

            docs = nodes["side:readme_md"]
            self.assertEqual(docs["kind"], "milestone")
            self.assertEqual(docs["category"], "docs")
            self.assertEqual(docs["skillHints"], ["documentation"])
            self.assertEqual(docs["estimateHours"], 0.5)

            utils = nodes["side:utils_py"]
            self.assertEqual(utils["estimateHours"], 0.5)
            self.assertEqual(utils["childIds"], [])

    def test_get_nodes_child_ids_filtered_to_graph_nodes(self):
        """childIds only references modules that exist in the graph."""
        h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            self._write_graph(root)
            h._get_nodes({"root": [root]})
            app = next(n for n in responses[0]["body"] if n["id"] == "side:app_py")
            self.assertEqual(app["childIds"], ["side:utils"])
            self.assertNotIn("side:missing_lib", app["childIds"])

    def test_get_nodes_category_override(self):
        """The category query param overrides every node's category."""
        h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            self._write_graph(root)
            h._get_nodes({"root": [root], "category": ["python"]})
            for n in responses[0]["body"]:
                self.assertEqual(n["category"], "python")

    def test_get_nodes_lang_override(self):
        """The lang query param replaces skill hints entirely."""
        h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            self._write_graph(root)
            h._get_nodes({"root": [root], "lang": ["python"]})
            for n in responses[0]["body"]:
                self.assertEqual(n["skillHints"], ["python"])

    def test_get_nodes_requires_root(self):
        """No root → 400 with a structured error body."""
        h, responses = self._handler()
        h._get_nodes({})
        self.assertEqual(responses[0]["status"], 400)
        self.assertEqual(responses[0]["body"], {"error": "root required"})

    def test_get_nodes_missing_graph_returns_empty_array(self):
        """A root without a .nodegraph.json is an empty import, not an error."""
        h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            h._get_nodes({"root": [root]})
            self.assertEqual(responses[0]["status"], 200)
            self.assertEqual(responses[0]["body"], [])

    def test_get_nodes_corrupt_graph_returns_empty_array(self):
        """A corrupt cached graph is treated as 'no graph' — the bridge serves
        the cache, and a corrupt cache is a cache-management problem, not a
        contract error for MythOS."""
        h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, ".nodegraph.json"), "w") as f:
                f.write("{not valid json !!")
            h._get_nodes({"root": [root]})
            self.assertEqual(responses[0]["status"], 200)
            self.assertEqual(responses[0]["body"], [])

    def test_get_infra_maps_fixture(self):
        """Infra nodes convert to SideNode shape with infra: id prefix."""
        h, responses = self._handler()
        with tempfile.TemporaryDirectory() as tmp:
            infra = os.path.join(tmp, "web-infra.json")
            fixture = {
                "nodes": [
                    {
                        "id": "worker-a",
                        "label": "worker A",
                        "kind": "worker",
                        "category": "cloudflare",
                        "detail": "does things",
                        "tech": ["cloudflare-workers", "d1"],
                        "estimateHours": 3,
                        "status": "live",
                        "childIds": ["db-x"],
                    },
                    {
                        "id": "db-x",
                        "label": "D1: x",
                        "kind": "database",
                        "category": "cloudflare",
                        "detail": "sqlite",
                        "tech": ["d1"],
                        "status": "live",
                    },
                ],
                "edges": [{"from": "worker-a", "to": "db-x", "type": "uses"}],
            }
            with open(infra, "w", encoding="utf-8") as f:
                json.dump(fixture, f)
            h._get_infra(infra)

            self.assertEqual(responses[0]["status"], 200)
            by_id = {n["id"]: n for n in responses[0]["body"]}
            self.assertEqual(set(by_id), {"infra:worker-a", "infra:db-x"})

            worker = by_id["infra:worker-a"]
            self.assertEqual(worker["label"], "worker A")
            self.assertEqual(worker["kind"], "worker")
            self.assertEqual(worker["category"], "cloudflare")
            self.assertEqual(worker["skillHints"], ["cloudflare-workers", "d1"])
            self.assertEqual(worker["estimateHours"], 3)
            self.assertEqual(worker["childIds"], ["infra:db-x"])
            self.assertEqual(worker["_source"], {
                "type": "web-infra",
                "status": "live",
                "tech": ["cloudflare-workers", "d1"],
            })

    def test_get_infra_missing_file_returns_empty_array(self):
        """Missing web-infra.json is an empty import, not an error."""
        h, responses = self._handler()
        with tempfile.TemporaryDirectory() as tmp:
            h._get_infra(os.path.join(tmp, "does-not-exist.json"))
            self.assertEqual(responses[0]["status"], 200)
            self.assertEqual(responses[0]["body"], [])

    def test_get_infra_corrupt_file_returns_500(self):
        """web-infra.json is committed source content; a parse failure is a
        defect and surfaces loudly rather than as an empty import."""
        h, responses = self._handler()
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "web-infra.json")
            with open(bad, "w") as f:
                f.write("[ broken {")
            h._get_infra(bad)
            self.assertEqual(responses[0]["status"], 500)
            self.assertIn("web-infra.json", responses[0]["body"]["error"])

    def test_get_infra_edges_pass_through(self):
        """Typed edges reach /api/infra as an additive per-node field.

        The response stays a bare array (MythOS's InfraView does
        `const data: InfraNode[] = await res.json()`), so edges cannot be a
        top-level field without breaking the frozen contract. Each node carries
        its OUTGOING edges as `edges: [{from, to, type}]`, ids `infra:`-prefixed
        to match the node id namespace; the union across nodes is the full edge
        set. Edges whose `from` is not a node are dropped (nothing to attach to).
        """
        h, responses = self._handler()
        with tempfile.TemporaryDirectory() as tmp:
            infra = os.path.join(tmp, "web-infra.json")
            fixture = {
                "nodes": [
                    {"id": "worker-a", "label": "worker A", "kind": "worker",
                     "category": "cloudflare", "detail": "", "tech": ["d1"],
                     "estimateHours": 0, "status": "live", "childIds": []},
                    {"id": "db-x", "label": "D1: x", "kind": "database",
                     "category": "cloudflare", "detail": "", "tech": ["d1"],
                     "estimateHours": 0, "status": "live"},
                    {"id": "sink", "label": "sink", "kind": "service",
                     "category": "cloudflare", "detail": "", "tech": [],
                     "estimateHours": 0, "status": "live"},
                ],
                "edges": [
                    {"from": "worker-a", "to": "db-x", "type": "reads/writes"},
                    {"from": "worker-a", "to": "sink", "type": "uses"},
                    {"from": "db-x", "to": "sink", "type": "feeds"},
                    {"from": "ghost", "to": "sink", "type": "dangling"},
                ],
            }
            with open(infra, "w", encoding="utf-8") as f:
                json.dump(fixture, f)
            h._get_infra(infra)

            self.assertEqual(responses[0]["status"], 200)
            # Contract preserved: still a bare array of SideNode objects.
            self.assertIsInstance(responses[0]["body"], list)
            by_id = {n["id"]: n for n in responses[0]["body"]}

            worker = by_id["infra:worker-a"]
            self.assertEqual(worker["edges"], [
                {"from": "infra:worker-a", "to": "infra:db-x", "type": "reads/writes"},
                {"from": "infra:worker-a", "to": "infra:sink", "type": "uses"},
            ])
            db = by_id["infra:db-x"]
            self.assertEqual(db["edges"], [
                {"from": "infra:db-x", "to": "infra:sink", "type": "feeds"},
            ])
            # Nodes with no outgoing edges still carry the additive field ([]).
            self.assertEqual(by_id["infra:sink"]["edges"], [])

            # The dangling edge (from: ghost, not a node) is not attached anywhere.
            all_edges = [e for n in responses[0]["body"] for e in n.get("edges", [])]
            self.assertNotIn({"from": "infra:ghost", "to": "infra:sink", "type": "dangling"},
                             all_edges)
            # Union over nodes is the full surviving edge set.
            self.assertEqual(len(all_edges), 3)

    def test_get_infra_graph_param_selects_file(self):
        """?graph=relay loads the committed relay.graph.json through the same
        mechanism web-infra.json uses (a named graph file at the repo root),
        and its typed edges pass through."""
        h, responses = self._handler()
        h._get_infra(graph="relay")
        self.assertEqual(responses[0]["status"], 200)
        self.assertIsInstance(responses[0]["body"], list)
        by_id = {n["id"]: n for n in responses[0]["body"]}
        self.assertIn("infra:opus-01", by_id)
        self.assertIn("infra:s1", by_id)
        opus = by_id["infra:opus-01"]
        self.assertTrue(opus["edges"])
        self.assertIn({"from": "infra:opus-01", "to": "infra:s1", "type": "dispatches"},
                      opus["edges"])

    def test_get_infra_unknown_graph_404(self):
        """An unregistered graph name is a malformed request, not an empty
        import — structured 404 like the other bridge error cases."""
        h, responses = self._handler()
        h._get_infra(graph="bogus")
        self.assertEqual(responses[0]["status"], 404)
        self.assertEqual(responses[0]["body"], {"error": "unknown graph: bogus"})

    def test_get_nodes_on_real_parse_output(self):
        """Round 3.2: lock parser imports/path conventions to the bridge's
        childIds + skillHints assumptions using real parse_project output —
        the two pipeline halves MythOS depends on, exercised end to end."""
        h, responses = self._handler()
        with _tmp_project(
            ("app.py", "import utils\nimport os\n\n\ndef main():\n    pass\n"),
            ("utils.py", "def helper():\n    return 1\n"),
            ("README.md", "# Synthetic\n"),
        ) as tmp:
            from parser.project_parser import parse_project
            parse_project(tmp, save_json=True)

            h._get_nodes({"root": [tmp]})
            self.assertEqual(responses[0]["status"], 200)
            by_id = {n["id"]: n for n in responses[0]["body"]}

            self.assertIn("side:app_py", by_id)
            self.assertIn("side:utils_py", by_id)

            app = by_id["side:app_py"]
            self.assertEqual(app["skillHints"][:2], ["python", "backend"])
            self.assertIn("os", app["skillHints"])
            self.assertEqual(app["childIds"], ["side:utils"])

            utils = by_id["side:utils_py"]
            self.assertEqual(utils["childIds"], [])

    def test_record_xp_writes_metrics(self):
        """POST /api/xp appends to .side-metrics.json via the real handler."""
        h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            mf = os.path.join(root, ".side-metrics.json")
            with open(mf, "w") as f:
                json.dump({}, f)

            h._record_xp({"root": root, "node_id": "side:app_py", "xp": 50,
                          "skills": ["python", "backend"]})
            self.assertEqual(responses[0]["status"], 200)
            self.assertEqual(responses[0]["body"], {"ok": True, "total_xp": 50})

            with open(mf, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["total_xp"], 50)
            self.assertEqual(len(data["xp_log"]), 1)
            entry = data["xp_log"][0]
            self.assertEqual(entry["node_id"], "side:app_py")
            self.assertEqual(entry["xp"], 50)
            self.assertEqual(entry["skills"], ["python", "backend"])
            self.assertIn("recorded_at", entry)

            h._record_xp({"root": root, "node_id": "side:utils_py", "xp": 25,
                          "skills": []})
            with open(mf, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["total_xp"], 75)
            self.assertEqual(len(data["xp_log"]), 2)

    def test_record_xp_requires_root_and_node_id(self):
        h, responses = self._handler()
        h._record_xp({"xp": 10})
        self.assertEqual(responses[0]["status"], 400)
        self.assertEqual(responses[0]["body"], {"error": "root and node_id required"})


class TestApiFs(unittest.TestCase):
    """Real-handler tests for the file picker endpoints.

    GET /api/fs  — one-level listing, GLOBAL_IGNORE-filtered, allow-list enforced
    POST /api/parse — register + parse in one action, visible counts on success.
    Uses the same stub _json/_error sink as TestSideNodeAdapter; no socket.
    """

    @staticmethod
    def _handler():
        import gui.server as server

        class _Stub(server.Handler):
            def __init__(self):
                pass

            def _json(self, data):
                responses.append({"status": 200, "body": data})

            def _error(self, code, msg):
                responses.append({"status": code, "body": {"error": str(msg)}})

        responses = []
        h = _Stub()
        return server, h, responses

    def _restrict_roots(self, server, root):
        """Point the allow-list at a hermetic dir; restore after the test."""
        old_roots = server.FS_ALLOW_ROOTS
        old_bookmarks = server.FS_BOOKMARKS
        server.FS_ALLOW_ROOTS = (root,)
        server.FS_BOOKMARKS = (root,)
        self.addCleanup(setattr, server, "FS_ALLOW_ROOTS", old_roots)
        self.addCleanup(setattr, server, "FS_BOOKMARKS", old_bookmarks)

    def test_fs_lists_one_level_filtered(self):
        server, h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            self._restrict_roots(server, root)
            # Ignored by the walker — must never appear in the picker.
            for d in ("node_modules", "__pycache__", ".venv"):
                os.makedirs(os.path.join(root, d), exist_ok=True)
            # A git root and a dir that already has a parsed graph.
            os.makedirs(os.path.join(root, "repo", ".git"), exist_ok=True)
            os.makedirs(os.path.join(root, "graphed"), exist_ok=True)
            with open(os.path.join(root, "graphed", ".nodegraph.json"), "w") as f:
                f.write("{}")
            with open(os.path.join(root, "main.py"), "w") as f:
                f.write("print('hi')\n")
            with open(os.path.join(root, "note.txt"), "w") as f:
                f.write("x\n")

            h._get_fs({"path": [root]})
            self.assertEqual(responses[0]["status"], 200)
            body = responses[0]["body"]
            self.assertEqual(body["path"], root)
            self.assertIsNone(body["parent"])   # top of an allow root — no going up
            self.assertEqual(body["allowed_roots"], [root])
            names = [e["name"] for e in body["entries"]]

            for bad in ("node_modules", "__pycache__", ".venv", ".nodegraph.json"):
                self.assertNotIn(bad, names, f"{bad} must be hidden from the picker")
            self.assertIn("main.py", names)
            self.assertIn("note.txt", names)

            by_name = {e["name"]: e for e in body["entries"]}
            repo = by_name["repo"]
            self.assertEqual(repo["type"], "dir")
            self.assertTrue(repo["is_git_root"])
            self.assertFalse(repo["has_graph"])
            graphed = by_name["graphed"]
            self.assertTrue(graphed["has_graph"])
            self.assertFalse(graphed["is_git_root"])
            for e in body["entries"]:
                self.assertIn("mtime", e)
                self.assertTrue(e["mtime"].endswith("Z"), e["mtime"])
            # Directories sort before files.
            kinds = [e["type"] for e in body["entries"]]
            self.assertNotIn("file", kinds[:kinds.index("dir") + 1])

    def test_fs_defaults_to_first_allowed_root(self):
        server, h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            self._restrict_roots(server, root)
            h._get_fs({"path": [""]})
            self.assertEqual(responses[0]["status"], 200)
            self.assertEqual(responses[0]["body"]["path"], root)

    def test_fs_rejects_allowlist_escape(self):
        server, h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            self._restrict_roots(server, root)
            h._get_fs({"path": ["/etc"]})
            self.assertEqual(responses[0]["status"], 403)
            self.assertIn("outside allowed roots",
                          responses[0]["body"]["error"])

    def test_fs_rejects_missing_dir(self):
        server, h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            self._restrict_roots(server, root)
            h._get_fs({"path": [os.path.join(root, "nope")]})
            self.assertEqual(responses[0]["status"], 404)

    def test_fs_hides_git_root_itself(self):
        server, h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            self._restrict_roots(server, root)
            os.makedirs(os.path.join(root, ".git"), exist_ok=True)
            h._get_fs({"path": [root]})
            names = [e["name"] for e in responses[0]["body"]["entries"]]
            self.assertNotIn(".git", names)

    def test_parse_registers_and_reports_counts(self):
        server, h, responses = self._handler()
        with tempfile.TemporaryDirectory() as root:
            # Point PROJECTS_FILE at a temp file so the real projects.json is
            # never touched by a test.
            old_pf = server.PROJECTS_FILE
            pf = os.path.join(root, "projects.json")
            server.PROJECTS_FILE = pf
            self.addCleanup(setattr, server, "PROJECTS_FILE", old_pf)
            with open(os.path.join(root, "main.py"), "w") as f:
                f.write("from utils import greet\nif __name__=='__main__':\n    greet()\n")
            os.makedirs(os.path.join(root, "utils"), exist_ok=True)
            with open(os.path.join(root, "utils", "__init__.py"), "w") as f:
                f.write("def greet():\n    print('hi')\n")

            h._parse({"path": root})
            self.assertEqual(responses[0]["status"], 200)
            body = responses[0]["body"]
            self.assertTrue(body["ok"], body)
            self.assertGreaterEqual(body["nodes"], 2)
            self.assertGreaterEqual(body["edges"], 1)
            self.assertGreaterEqual(body["ms"], 0)

            # Registered in the (temp) project list.
            with open(pf, encoding="utf-8") as f:
                projs = json.load(f)
            self.assertTrue(any(p["path"] == root for p in projs))

    def test_parse_rejects_invalid_path(self):
        server, h, responses = self._handler()
        h._parse({"path": "/definitely/not/a/real/dir"})
        self.assertEqual(responses[0]["status"], 400)
        self.assertIn("invalid path", responses[0]["body"]["error"])


class TestWebInfraIntegrity(unittest.TestCase):
    """Structural validation of the committed web-infra.json (round 2.3)."""

    def test_edges_and_child_ids_reference_existing_nodes(self):
        from gui.server import _ROOT_DIR
        with open(os.path.join(_ROOT_DIR, "web-infra.json"), encoding="utf-8") as f:
            data = json.load(f)
        ids = {n["id"] for n in data.get("nodes", [])}

        for n in data.get("nodes", []):
            self.assertIn("id", n)
            self.assertIn("label", n)
            self.assertIn("kind", n)
            for cid in n.get("childIds", []):
                self.assertIn(cid, ids, f"node {n['id']} childId {cid} has no node")

        for e in data.get("edges", []):
            self.assertIn(e["from"], ids, f"edge from {e['from']} has no node")
            self.assertIn(e["to"], ids, f"edge to {e['to']} has no node")


class TestPlansGraphGenerator(unittest.TestCase):
    """The vault -> plans.graph.json generator (bin/relay-graph.py)."""

    def _load_generator(self):
        import importlib.util
        import pathlib
        spec = importlib.util.spec_from_file_location(
            "relay_graph",
            pathlib.Path(__file__).resolve().parent.parent / "bin" / "relay-graph.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _make_vault(self, tmp):
        projects = os.path.join(tmp, "20-projects")
        tasks = os.path.join(tmp, "70-tasks")
        os.makedirs(projects)
        os.makedirs(tasks)
        with open(os.path.join(projects, "alpha.md"), "w", encoding="utf-8") as f:
            f.write("---\ntype: project\ntier: top\nstage: nigredo\n"
                    "status: active\nblocked-by: [\"[[beta]]\"]\n---\n"
                    "# Alpha project\n\n**One line.** The alpha thing.\n")
        with open(os.path.join(projects, "beta.md"), "w", encoding="utf-8") as f:
            f.write("---\ntype: project\nstatus: queued\n---\n# Beta project\n")
        with open(os.path.join(projects, "not-a-project.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: generated snapshot\ndate: 2026-07-26\n---\n")
        with open(os.path.join(tasks, "task-a.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: Task A\nstatus: open\nprojects: [\"[[alpha]]\"]\n---\n")
        with open(os.path.join(tasks, "task-b.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: Task B\nstatus: open\nprojects: []\n---\n")
        with open(os.path.join(tasks, "task-c.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: Task C\nstatus: open\nprojects: [\"[[beta]]\"]\n---\n")
        return projects, tasks

    def test_generator_builds_nodes_and_edges(self):
        gen = self._load_generator()
        with tempfile.TemporaryDirectory() as tmp:
            self._make_vault(tmp)
            graph = gen.build_graph(gen.collect_notes(tmp))

        nodes = {n["id"]: n for n in graph["nodes"]}
        self.assertIn("pro-alpha", nodes)
        self.assertIn("tas-task-a", nodes)
        self.assertNotIn("pro-not-a-project", nodes)
        self.assertNotIn("tas-not-a-project", nodes)

        # tier -> category, status maps through, stage carried.
        self.assertEqual(nodes["pro-alpha"]["category"], "top")
        self.assertEqual(nodes["pro-alpha"]["stage"], "nigredo")
        self.assertEqual(nodes["pro-alpha"]["status"], "active")
        self.assertEqual(nodes["pro-beta"]["category"], "unsorted")
        self.assertEqual(nodes["tas-task-a"]["category"], "unsorted")

        # blocked-by -> blocks edge (blocker -> blocked).
        self.assertIn(
            {"from": "pro-beta", "to": "pro-alpha", "type": "blocks"},
            graph["edges"],
        )
        # projects: wikilink -> schedules edge (project -> task).
        self.assertIn(
            {"from": "pro-alpha", "to": "tas-task-a", "type": "schedules"},
            graph["edges"],
        )
        self.assertIn(
            {"from": "pro-beta", "to": "tas-task-c", "type": "schedules"},
            graph["edges"],
        )
        # A task with no projects: link has no schedules edge.
        self.assertNotIn(
            {"from": "pro-alpha", "to": "tas-task-b", "type": "schedules"},
            graph["edges"],
        )

    def test_generator_drops_dangling_wikilinks(self):
        gen = self._load_generator()
        with tempfile.TemporaryDirectory() as tmp:
            projects = os.path.join(tmp, "20-projects")
            tasks = os.path.join(tmp, "70-tasks")
            os.makedirs(projects)
            os.makedirs(tasks)
            with open(os.path.join(projects, "alpha.md"), "w", encoding="utf-8") as f:
                f.write("---\ntype: project\nstatus: active\n"
                        "blocked-by: [\"[[missing]]\"]\n---\n")
            with open(os.path.join(tasks, "t.md"), "w", encoding="utf-8") as f:
                f.write("---\ntitle: T\nstatus: open\nprojects: [\"[[ghost]]\"]\n---\n")
            graph = gen.build_graph(gen.collect_notes(tmp))
        self.assertEqual(graph["edges"], [])
        self.assertIn("[[missing]] resolves to no note", "\n".join(graph["warnings"]))
        self.assertIn("[[ghost]] resolves to no note", "\n".join(graph["warnings"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)

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
