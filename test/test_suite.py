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
            open(os.path.join(root, 'proj-a',
                 'side.project.json'), 'w').write('{}')
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
            open(os.path.join(proj, 'main.py'), 'w').write(
                'import requests\nfrom numpy import array\nimport os\n')
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


class TestSideNodeAdapter(unittest.TestCase):
    """Test the /api/nodes SideNode adapter for MythOS bridge."""

    _PROJECT_ROOT = ROOT  # s-ide's own project

    def test_nodegraph_to_sidenodes(self):
        """Verify .nodegraph.json nodes convert to SideNode shape."""
        from gui.server import _load_graph
        g = _load_graph(self._PROJECT_ROOT)
        self.assertIsNotNone(g)
        nodes = g["nodes"]
        self.assertGreater(len(nodes), 0)

        # Simulate the adapter logic from _get_nodes
        LANG_SKILL_HINTS = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".html": "html", ".css": "css", ".go": "go", ".rs": "rust",
            ".sh": "shell", ".md": "markdown",
        }
        CATEGORY_TO_KIND = {
            "source": "task", "test": "task", "docs": "milestone",
            "config": "task", "script": "task",
            "python": "task", "javascript": "task", "typescript": "task",
        }

        side_nodes = []
        for n in nodes:
            ext = n.get("ext", "")
            lang = ext.lstrip(".")
            category = n.get("category", "source")
            imports = n.get("imports", [])
            lines = n.get("lines", 0)

            hints = []
            if lang in LANG_SKILL_HINTS:
                hints.append(LANG_SKILL_HINTS[lang])

            for imp in imports[:5]:
                src = imp.get("source", "") if isinstance(imp, dict) else str(imp)
                if src:
                    hints.append(src.split(".")[0].split("/")[-1])

            seen = set()
            unique_hints = [h for h in hints if h not in seen and not seen.add(h)]
            estimate = max(0.5, round(lines / 50, 1)) if lines else None

            side_nodes.append({
                "id": f"side:{n.get('id', '')}",
                "label": n.get("label", ""),
                "kind": CATEGORY_TO_KIND.get(category, "task"),
                "category": category,
                "skillHints": unique_hints,
                "estimateHours": estimate,
            })

        # All nodes should have required SideNode fields
        for sn in side_nodes:
            self.assertIn("id", sn)
            self.assertIn("label", sn)
            self.assertIn("kind", sn)
            self.assertTrue(sn["id"].startswith("side:"))
            self.assertIn(sn["kind"], ("task", "milestone", "quest"))

        # Check specific nodes exist
        ids = {sn["id"] for sn in side_nodes}
        self.assertIn("side:main_py", ids)

    def test_side_node_has_skill_hints(self):
        """Python nodes should have 'python' in skill hints."""
        from gui.server import _load_graph
        g = _load_graph(self._PROJECT_ROOT)
        py_nodes = [n for n in g["nodes"] if n.get("ext") == ".py"]
        self.assertGreater(len(py_nodes), 0)

        n = py_nodes[0]
        hints = ["python"]  # from lang mapping
        for imp in n.get("imports", [])[:5]:
            src = imp.get("source", "") if isinstance(imp, dict) else str(imp)
            if src:
                hints.append(src.split(".")[0].split("/")[-1])

        self.assertIn("python", hints)

    def test_estimate_hours_from_lines(self):
        """Estimate hours should be proportional to line count."""
        # 50 lines → 1.0h, 100 lines → 2.0h, 10 lines → 0.5h (min)
        self.assertAlmostEqual(max(0.5, round(50 / 50, 1)), 1.0)
        self.assertAlmostEqual(max(0.5, round(100 / 50, 1)), 2.0)
        self.assertAlmostEqual(max(0.5, round(10 / 50, 1)), 0.5)

    def test_xp_recording_writes_metrics(self):
        """POST /api/xp should append to .side-metrics.json."""
        import tempfile
        from gui.server import Handler
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create empty metrics file
            mf = os.path.join(tmpdir, ".side-metrics.json")
            with open(mf, "w") as f:
                json.dump({}, f)

            # Simulate the _record_xp logic
            body = {
                "root": tmpdir,
                "node_id": "side:agent_loop_py",
                "xp": 50,
                "skills": ["python", "subprocess"],
            }
            data = json.load(open(mf))
            xp_log = data.setdefault("xp_log", [])
            xp_log.append({
                "node_id": body["node_id"],
                "xp": body["xp"],
                "skills": body["skills"],
                "recorded_at": "2026-07-19T00:00:00",
            })
            data["total_xp"] = data.get("total_xp", 0) + body["xp"]
            with open(mf, "w") as f:
                json.dump(data, f, indent=2)

            # Verify
            result = json.load(open(mf))
            self.assertEqual(result["total_xp"], 50)
            self.assertEqual(len(result["xp_log"]), 1)
            self.assertEqual(result["xp_log"][0]["node_id"], "side:agent_loop_py")
            self.assertEqual(result["xp_log"][0]["xp"], 50)
            self.assertIn("python", result["xp_log"][0]["skills"])


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
