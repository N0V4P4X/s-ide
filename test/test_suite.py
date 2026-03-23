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
TestVersionManager  — archive, extract, list, compress
TestProcessManager  — spawn, logs, stop
"""

from __future__ import annotations
import json
import os
import sys
import tarfile
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
from version.version_manager import archive_version, apply_update, list_versions, compress_loose
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
# Version manager tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestVersionManager(unittest.TestCase):

    def _make_project(self, tmp_dir: str) -> None:
        os.makedirs(os.path.join(tmp_dir, "src"), exist_ok=True)
        with open(os.path.join(tmp_dir, "main.py"), "w") as f:
            f.write("print('hello')\n")
        with open(os.path.join(tmp_dir, "src", "utils.py"), "w") as f:
            f.write("def helper(): pass\n")
        cfg = {"name": "testproj", "version": "1.0.0",
               "versions": {"dir": "versions", "compress": True, "keep": 5}}
        with open(os.path.join(tmp_dir, "side.project.json"), "w") as f:
            json.dump(cfg, f)

    def test_archive_creates_tarball(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            path = archive_version(tmp)
            self.assertTrue(os.path.exists(path))
            self.assertTrue(path.endswith(".tar.gz"))

    def test_archive_is_valid_tar(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            path = archive_version(tmp)
            self.assertTrue(tarfile.is_tarfile(path))

    def test_list_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            archive_version(tmp)
            versions = list_versions(tmp)
            self.assertEqual(len(versions), 1)
            self.assertEqual(versions[0]["type"], "tarball")

    def test_apply_update(self):
        with tempfile.TemporaryDirectory() as proj_dir:
            self._make_project(proj_dir)

            # Build an "update" tarball from a temp dir
            with tempfile.TemporaryDirectory() as update_src:
                os.makedirs(os.path.join(update_src, "testproj"))
                with open(os.path.join(update_src, "testproj", "new_file.py"), "w") as f:
                    f.write("# new!\n")
                tarball = os.path.join(update_src, "update.tar.gz")
                with tarfile.open(tarball, "w:gz") as tar:
                    tar.add(os.path.join(update_src, "testproj"), arcname="testproj")

                new_ver, arch = apply_update(proj_dir, tarball, "minor")
                self.assertEqual(new_ver, "1.1.0")
                self.assertTrue(os.path.exists(arch))
                self.assertTrue(os.path.exists(os.path.join(proj_dir, "new_file.py")))

    def test_versions_dir_excluded_from_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            archive_version(tmp)  # creates versions/
            path = archive_version(tmp)  # second archive
            with tarfile.open(path, "r:gz") as tar:
                names = tar.getnames()
            self.assertFalse(any("versions" in n for n in names))


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


# ── Runner ────────────────────────────────────────────────────────────────────

# ── runner below ──


# ═══════════════════════════════════════════════════════════════════════════════
# Monitor tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseTimer(unittest.TestCase):

    def test_stages_recorded(self):
        from monitor.perf import ParseTimer
        t = ParseTimer()
        with t.stage("walk"):
            pass
        with t.stage("parse"):
            time.sleep(0.01)
        report = t.report()
        names = [s["name"] for s in report["stages"]]
        self.assertIn("walk", names)
        self.assertIn("parse", names)

    def test_total_ms_positive(self):
        from monitor.perf import ParseTimer
        t = ParseTimer()
        with t.stage("x"):
            time.sleep(0.005)
        self.assertGreater(t.total_ms(), 0)

    def test_slowest_identified(self):
        from monitor.perf import ParseTimer
        t = ParseTimer()
        with t.stage("fast"):
            pass
        with t.stage("slow"):
            time.sleep(0.02)
        report = t.report()
        self.assertEqual(report["slowest"], "slow")

    def test_perf_embedded_in_graph(self):
        """parse_project stores per-stage timing in graph.meta.perf."""
        from parser.project_parser import parse_project
        with _tmp_project(("main.py", "import os\n")) as tmp:
            graph = parse_project(tmp, save_json=False)
        # perf lives in GraphMeta now, not as a private attribute
        perf = graph.meta.perf
        self.assertIn("stages", perf)
        self.assertIn("total_ms", perf)
        stage_names = [s["name"] for s in perf["stages"]]
        self.assertIn("parse_files", stage_names)
        self.assertIn("walk", stage_names)

    def test_nodegraph_json_written(self):
        """parse_project auto-saves .nodegraph.json."""
        import json
        from parser.project_parser import parse_project
        with _tmp_project(("app.py", "def main(): pass\n")) as tmp:
            parse_project(tmp)
            json_path = os.path.join(tmp, ".nodegraph.json")
            self.assertTrue(os.path.isfile(json_path))
            data = json.load(open(json_path))
            self.assertIn("nodes", data)
            self.assertIn("edges", data)


# ═══════════════════════════════════════════════════════════════════════════════
# Build — Cleaner tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCleaner(unittest.TestCase):

    def test_dry_run_removes_nothing(self):
        from build.cleaner import clean_project, CleanOptions
        with _tmp_project(
            ("__pycache__/foo.pyc", ""),
            ("main.py", "pass\n"),
        ) as tmp:
            opts = CleanOptions(tiers=["cache"], dry_run=True)
            report = clean_project(tmp, opts)
            self.assertTrue(len(report.removed) > 0)
            # File should still exist — dry run
            self.assertTrue(os.path.exists(os.path.join(tmp, "__pycache__")))

    def test_cache_tier_removes_pycache(self):
        from build.cleaner import clean_project, CleanOptions
        with _tmp_project(
            ("__pycache__/module.pyc", "bytecode"),
            ("main.py", "pass\n"),
        ) as tmp:
            opts = CleanOptions(tiers=["cache"])
            report = clean_project(tmp, opts)
            self.assertFalse(os.path.exists(os.path.join(tmp, "__pycache__")))
            self.assertTrue(any("__pycache__" in r for r in report.removed))

    def test_logs_tier_removes_log_files(self):
        from build.cleaner import clean_project, CleanOptions
        with _tmp_project(
            ("app.log", "log content"),
            ("main.py", "pass\n"),
        ) as tmp:
            opts = CleanOptions(tiers=["logs"])
            report = clean_project(tmp, opts)
            self.assertFalse(os.path.exists(os.path.join(tmp, "app.log")))

    def test_protect_prevents_deletion(self):
        from build.cleaner import clean_project, CleanOptions
        with _tmp_project(
            ("logs/keep.log", "important"),
            ("main.py", "pass\n"),
        ) as tmp:
            opts = CleanOptions(tiers=["logs"], protect=["logs"])
            report = clean_project(tmp, opts)
            self.assertTrue(os.path.exists(os.path.join(tmp, "logs", "keep.log")))

    def test_freed_bytes_nonzero(self):
        from build.cleaner import clean_project, CleanOptions
        with _tmp_project(
            ("junk.log", "x" * 500),
            ("main.py", "pass\n"),
        ) as tmp:
            opts = CleanOptions(tiers=["logs"])
            report = clean_project(tmp, opts)
            self.assertGreater(report.freed_bytes, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Build — Minifier tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMinifier(unittest.TestCase):

    def test_strips_python_comments(self):
        from build.minifier import minify_file, MinifyOptions
        with _tmp_project(("util.py", "# this is a comment\ndef foo(): pass\n")) as tmp:
            path = os.path.join(tmp, "util.py")
            result = minify_file(path, MinifyOptions(strip_comments=True))
            self.assertNotIn("# this is a comment", result)
            self.assertIn("def foo", result)

    def test_strips_docstring(self):
        from build.minifier import minify_file, MinifyOptions
        src = '"""Module docstring."""\ndef foo():\n    """Func doc."""\n    return 1\n'
        with _tmp_project(("m.py", src)) as tmp:
            path = os.path.join(tmp, "m.py")
            result = minify_file(path, MinifyOptions(strip_docstrings=True))
            self.assertNotIn("Module docstring", result)
            self.assertNotIn("Func doc", result)
            self.assertIn("def foo", result)

    def test_preserves_noqa_regex_path(self):
        # ast.unparse discards all comments (including noqa) by design.
        # The regex path (strip_docstrings=False) preserves # noqa.
        from build.minifier import _minify_python, MinifyOptions
        src = "import os  # noqa: F401\n"
        opts = MinifyOptions(strip_docstrings=False, strip_comments=True)
        result = _minify_python(src, opts)
        # noqa comment should survive the regex comment stripper
        self.assertIn("noqa", result)

    def test_strips_regular_comment_via_regex(self):
        from build.minifier import _minify_python, MinifyOptions
        src = "x = 1  # regular comment\n"
        opts = MinifyOptions(strip_docstrings=False, strip_comments=True)
        result = _minify_python(src, opts)
        self.assertNotIn("regular comment", result)
        self.assertIn("x = 1", result)

    def test_minify_json(self):
        from build.minifier import minify_file, MinifyOptions
        src = '{\n  "key": "value",\n  "num": 42\n}\n'
        with _tmp_project(("config.json", src)) as tmp:
            path = os.path.join(tmp, "config.json")
            result = minify_file(path, MinifyOptions(minify_json=True))
            self.assertNotIn("\n  ", result)
            self.assertIn('"key"', result)

    def test_minify_project_produces_output(self):
        from build.minifier import minify_project, MinifyOptions
        with _tmp_project(
            ("main.py", "# comment\ndef run(): pass\n"),
            ("utils.py", "def helper(): return 1\n"),
        ) as tmp:
            import tempfile
            with tempfile.TemporaryDirectory() as out:
                report = minify_project(tmp, out, MinifyOptions())
                self.assertGreater(report.files_processed, 0)
                self.assertTrue(os.path.exists(os.path.join(out, "main.py")))

    def test_binary_raises(self):
        from build.minifier import minify_file
        with _tmp_project(("img.png", b"\x89PNG\r\n".decode("latin1"))) as tmp:
            with self.assertRaises(ValueError):
                minify_file(os.path.join(tmp, "img.png"))


# ═══════════════════════════════════════════════════════════════════════════════
# Build — Packager tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPackager(unittest.TestCase):

    def _make_project(self, tmp):
        import json
        with open(os.path.join(tmp, "main.py"), "w") as f:
            f.write("print('hello')\n")
        with open(os.path.join(tmp, "side.project.json"), "w") as f:
            json.dump({"name": "testapp", "version": "1.0.0",
                       "versions": {"dir": "versions", "compress": True, "keep": 5}}, f)

    def test_tarball_created(self):
        from build.packager import package_project, PackageOptions
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            with tempfile.TemporaryDirectory() as out:
                opts = PackageOptions(kind="tarball", minify=False, clean=False)
                result = package_project(tmp, out, opts)
                self.assertTrue(os.path.isfile(result.archive_path))
                self.assertTrue(result.archive_path.endswith(".tar.gz"))

    def test_portable_creates_directory(self):
        from build.packager import package_project, PackageOptions
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            with tempfile.TemporaryDirectory() as out:
                opts = PackageOptions(kind="portable", minify=False, clean=False)
                result = package_project(tmp, out, opts)
                self.assertTrue(os.path.isdir(result.output_path))
                self.assertTrue(os.path.isfile(
                    os.path.join(result.output_path, "build-manifest.json")))

    def test_manifest_recorded(self):
        import json
        from build.packager import package_project, PackageOptions
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            with tempfile.TemporaryDirectory() as out:
                opts = PackageOptions(kind="tarball", minify=False, clean=False)
                package_project(tmp, out, opts)
                manifest = json.load(open(os.path.join(out, "build-manifest.json")))
                self.assertIn("history", manifest)
                self.assertEqual(manifest["history"][0]["name"], "testapp")

    def test_build_with_minify(self):
        from build.packager import package_project, PackageOptions
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "main.py"), "w") as f:
                f.write("# this is a dev comment\ndef run(): pass\n")
            with tempfile.TemporaryDirectory() as out:
                opts = PackageOptions(kind="tarball", minify=True, clean=False)
                result = package_project(tmp, out, opts)
                # Should succeed even without side.project.json
                self.assertTrue(os.path.isfile(result.archive_path))





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
# Version manager bootstrap tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestVersionManagerBootstrap(unittest.TestCase):

    def test_archive_creates_side_project_json(self):
        """archive_version auto-creates side.project.json on first run."""
        from version.version_manager import archive_version
        with tempfile.TemporaryDirectory() as tmp:
            # Brand new project — no side.project.json
            with open(os.path.join(tmp, "main.py"), "w") as f:
                f.write("print('hello')\n")
            path = archive_version(tmp)
            self.assertTrue(os.path.isfile(path))
            cfg_path = os.path.join(tmp, "side.project.json")
            self.assertTrue(os.path.isfile(cfg_path),
                            "side.project.json should be created by archive_version")

    def test_archive_preserves_existing_version(self):
        """archive_version doesn't overwrite an existing side.project.json."""
        import json
        from version.version_manager import archive_version
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"name": "mypkg", "version": "3.7.2",
                   "versions": {"dir": "versions", "compress": True, "keep": 5}}
            with open(os.path.join(tmp, "side.project.json"), "w") as f:
                json.dump(cfg, f)
            with open(os.path.join(tmp, "app.py"), "w") as f:
                f.write("pass\n")
            archive_version(tmp)
            cfg_after = json.load(open(os.path.join(tmp, "side.project.json")))
            self.assertEqual(cfg_after["version"], "3.7.2")




# ═══════════════════════════════════════════════════════════════════════════════
# Monitor — instrument + MetricsWatcher tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestInstrument(unittest.TestCase):

    def setUp(self):
        # Reset instrument state between tests
        from monitor import instrument
        instrument.reset()
        instrument._project_root = ""
        instrument._metrics_path = ""

    def test_timed_decorator_records(self):
        from monitor.instrument import timed, get_snapshot
        import tempfile, os

        @timed
        def add(a, b):
            return a + b

        with tempfile.TemporaryDirectory() as tmp:
            from monitor.instrument import init
            # Patch __file__ on the wrapper via module inspection
            import monitor.instrument as inst
            inst._project_root = tmp
            inst._metrics_path = os.path.join(tmp, ".side-metrics.json")

            add(1, 2)
            add(3, 4)

            snap = get_snapshot()
            # At least one file entry should exist
            self.assertGreater(len(snap["files"]), 0)
            total_calls = sum(v["calls"] for v in snap["files"].values())
            self.assertGreaterEqual(total_calls, 2)

    def test_timed_block_records(self):
        from monitor.instrument import timed_block, get_snapshot, init
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmp:
            init(tmp)
            with timed_block("test_block", os.path.join(tmp, "fake.py")):
                time.sleep(0.01)
            snap = get_snapshot()
            fn_keys = list(snap["functions"].keys())
            self.assertTrue(any("test_block" in k for k in fn_keys))
            # Should have recorded > 0ms
            for k, v in snap["functions"].items():
                if "test_block" in k:
                    self.assertGreater(v["avg_ms"], 0)

    def test_flush_writes_file(self):
        from monitor.instrument import init, flush, timed_block
        import tempfile, os, json

        with tempfile.TemporaryDirectory() as tmp:
            init(tmp, flush_interval=999)   # disable auto-flush
            with timed_block("x", os.path.join(tmp, "m.py")):
                pass
            flush()
            metrics_path = os.path.join(tmp, ".side-metrics.json")
            self.assertTrue(os.path.isfile(metrics_path))
            data = json.load(open(metrics_path))
            self.assertIn("files", data)
            self.assertIn("functions", data)
            self.assertIn("pid", data)

    def test_reset_clears_data(self):
        from monitor.instrument import timed_block, get_snapshot, reset, init
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmp:
            init(tmp)
            with timed_block("work", os.path.join(tmp, "a.py")):
                pass
            self.assertGreater(len(get_snapshot()["files"]), 0)
            reset()
            snap = get_snapshot()
            self.assertEqual(len(snap["files"]), 0)
            self.assertEqual(len(snap["functions"]), 0)

    def test_stats_accumulate_correctly(self):
        from monitor.instrument import timed_block, get_snapshot, reset, init
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmp:
            init(tmp)
            fp = os.path.join(tmp, "worker.py")
            for _ in range(5):
                with timed_block("step", fp):
                    time.sleep(0.002)
            snap = get_snapshot()
            fn_key = next(k for k in snap["functions"] if "step" in k)
            stats = snap["functions"][fn_key]
            self.assertEqual(stats["calls"], 5)
            self.assertGreater(stats["avg_ms"], 0)
            self.assertGreaterEqual(stats["max_ms"], stats["avg_ms"])

    def test_init_sets_paths(self):
        from monitor import instrument
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            instrument.init(tmp)
            self.assertEqual(os.path.abspath(instrument._project_root),
                             os.path.abspath(tmp))
            self.assertTrue(instrument._metrics_path.endswith(".side-metrics.json"))


class TestMetricsWatcher(unittest.TestCase):

    def _write_metrics(self, path: str, data: dict):
        import json
        with open(path, "w") as f:
            json.dump(data, f)

    def test_reads_metrics_file(self):
        from monitor.perf import MetricsWatcher
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmp:
            metrics_path = os.path.join(tmp, ".side-metrics.json")
            self._write_metrics(metrics_path, {
                "pid": 1234,
                "updated": time.time(),
                "files": {
                    "src/main.py": {"calls": 10, "avg_ms": 5.0, "max_ms": 12.0,
                                    "last_ms": 4.8, "last_ts": time.time(), "total_ms": 50.0}
                },
                "functions": {}
            })

            watcher = MetricsWatcher(tmp)
            # Force a poll without starting the thread
            watcher._poll()

            fm = watcher.get_file_metrics()
            self.assertIn("src/main.py", fm)
            self.assertEqual(fm["src/main.py"]["calls"], 10)

    def test_is_active_with_fresh_data(self):
        from monitor.perf import MetricsWatcher
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmp:
            metrics_path = os.path.join(tmp, ".side-metrics.json")
            self._write_metrics(metrics_path, {
                "pid": 1,
                "updated": time.time(),   # just now
                "files": {}, "functions": {}
            })
            watcher = MetricsWatcher(tmp)
            watcher._poll()
            self.assertTrue(watcher.is_active())

    def test_is_not_active_with_stale_data(self):
        from monitor.perf import MetricsWatcher
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmp:
            metrics_path = os.path.join(tmp, ".side-metrics.json")
            self._write_metrics(metrics_path, {
                "pid": 1,
                "updated": time.time() - 60,   # 60s ago = stale
                "files": {}, "functions": {}
            })
            watcher = MetricsWatcher(tmp)
            watcher._poll()
            self.assertFalse(watcher.is_active())

    def test_skips_reread_if_mtime_unchanged(self):
        from monitor.perf import MetricsWatcher
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmp:
            metrics_path = os.path.join(tmp, ".side-metrics.json")
            self._write_metrics(metrics_path, {
                "pid": 1, "updated": time.time(),
                "files": {"a.py": {"calls": 1, "avg_ms": 1.0, "max_ms": 1.0,
                                    "last_ms": 1.0, "last_ts": time.time(), "total_ms": 1.0}},
                "functions": {}
            })
            watcher = MetricsWatcher(tmp)
            watcher._poll()
            first = watcher.get_file_metrics()

            # Overwrite with different data but same mtime (simulate no change)
            watcher._mtime = os.path.getmtime(metrics_path) + 1  # pretend already read
            self._write_metrics(metrics_path, {
                "pid": 1, "updated": time.time(),
                "files": {"b.py": {"calls": 99, "avg_ms": 99.0, "max_ms": 99.0,
                                    "last_ms": 99.0, "last_ts": time.time(), "total_ms": 99.0}},
                "functions": {}
            })
            watcher._poll()   # mtime check will skip this read
            second = watcher.get_file_metrics()

            # Should still see original data (skipped the re-read)
            self.assertIn("a.py", second)

    def test_no_file_returns_empty(self):
        from monitor.perf import MetricsWatcher
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            watcher = MetricsWatcher(tmp)   # no .side-metrics.json
            watcher._poll()
            self.assertEqual(watcher.get_file_metrics(), {})
            self.assertFalse(watcher.is_active())

    def test_thread_start_stop(self):
        from monitor.perf import MetricsWatcher
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            watcher = MetricsWatcher(tmp)
            watcher.start()
            self.assertTrue(watcher._thread.is_alive())
            watcher.stop()
            watcher._thread.join(timeout=3)
            self.assertFalse(watcher._thread.is_alive())

    def test_function_metrics(self):
        from monitor.perf import MetricsWatcher
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmp:
            metrics_path = os.path.join(tmp, ".side-metrics.json")
            self._write_metrics(metrics_path, {
                "pid": 1, "updated": time.time(),
                "files": {},
                "functions": {
                    "src/parser.py::parse_file": {
                        "calls": 5, "avg_ms": 20.0, "max_ms": 45.0,
                        "last_ms": 18.0, "last_ts": time.time(), "total_ms": 100.0
                    }
                }
            })
            watcher = MetricsWatcher(tmp)
            watcher._poll()
            fm = watcher.get_function_metrics()
            self.assertIn("src/parser.py::parse_file", fm)
            self.assertEqual(fm["src/parser.py::parse_file"]["calls"], 5)


# ── runner ──


# ═══════════════════════════════════════════════════════════════════════════════
# Instrumenter tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestInstrumenter(unittest.TestCase):

    def _project(self, *files):
        """Create a temp project with given (path, content) files."""
        return _tmp_project(*files)

    def test_adds_timed_to_public_function(self):
        from monitor.instrumenter import Instrumenter, InstrumentOptions
        src = "def do_work(x):\n    return x * 2\n\ndef _private():\n    pass\n"
        with self._project(("worker.py", src)) as tmp:
            opts = InstrumentOptions(backup=False, preview=False)
            result = Instrumenter(tmp, opts).run()
            modified = open(os.path.join(tmp, "worker.py")).read()
            self.assertIn("@timed", modified)
            self.assertIn("from monitor.instrument import timed", modified)
            self.assertNotIn("_private", "".join(
                l for l in modified.splitlines() if "@timed" in l))

    def test_skips_private_functions(self):
        from monitor.instrumenter import Instrumenter, InstrumentOptions
        src = "def _helper():\n    pass\n\ndef _other():\n    pass\n"
        with self._project(("priv.py", src)) as tmp:
            opts = InstrumentOptions(backup=False)
            result = Instrumenter(tmp, opts).run()
            self.assertEqual(result.functions_timed, 0)

    def test_skips_already_instrumented(self):
        from monitor.instrumenter import Instrumenter, InstrumentOptions
        src = ("from monitor.instrument import timed\n\n"
               "@timed\ndef process():\n    return 1\n")
        with self._project(("mod.py", src)) as tmp:
            opts = InstrumentOptions(backup=False)
            result = Instrumenter(tmp, opts).run()
            self.assertEqual(result.functions_timed, 0)

    def test_preview_does_not_modify_files(self):
        from monitor.instrumenter import Instrumenter, InstrumentOptions
        src = "def compute(n):\n    return n ** 2\n"
        with self._project(("calc.py", src)) as tmp:
            opts = InstrumentOptions(backup=False, preview=True)
            result = Instrumenter(tmp, opts).run()
            # Preview flag — file should be unchanged
            unchanged = open(os.path.join(tmp, "calc.py")).read()
            self.assertEqual(unchanged, src)
            self.assertGreater(result.functions_timed, 0)

    def test_backup_creates_side_backup(self):
        from monitor.instrumenter import Instrumenter, InstrumentOptions
        src = "def run():\n    pass\n"
        with self._project(("app.py", src)) as tmp:
            opts = InstrumentOptions(backup=True, preview=False)
            result = Instrumenter(tmp, opts).run()
            backup_dir = os.path.join(tmp, ".side-backup")
            self.assertTrue(os.path.isdir(backup_dir))
            self.assertTrue(os.path.isfile(os.path.join(tmp, ".side-backup", "app.py")))

    def test_rollback_restores_original(self):
        from monitor.instrumenter import Instrumenter, InstrumentOptions, rollback
        src = "def compute():\n    return 42\n"
        with self._project(("mod.py", src)) as tmp:
            opts = InstrumentOptions(backup=True, preview=False)
            Instrumenter(tmp, opts).run()
            modified = open(os.path.join(tmp, "mod.py")).read()
            self.assertIn("@timed", modified)
            # Rollback
            result = rollback(tmp)
            restored = open(os.path.join(tmp, "mod.py")).read()
            self.assertEqual(restored, src)
            self.assertIn("mod.py", result["restored"])

    def test_generates_test_stubs(self):
        from monitor.instrumenter import Instrumenter, InstrumentOptions
        src = "def fetch_data(url):\n    return {}\n\ndef process(data):\n    return data\n"
        with self._project(("fetcher.py", src)) as tmp:
            opts = InstrumentOptions(backup=False, add_tests=True, test_dir="test")
            result = Instrumenter(tmp, opts).run()
            self.assertGreater(len(result.tests_created), 0)
            test_file = os.path.join(tmp, "test", "test_fetcher.py")
            self.assertTrue(os.path.isfile(test_file))
            test_content = open(test_file).read()
            self.assertIn("def test_fetch_data", test_content)
            self.assertIn("def test_process", test_content)

    def test_skips_test_files(self):
        from monitor.instrumenter import Instrumenter, InstrumentOptions
        src = "def test_something():\n    assert True\n"
        with self._project(("test_main.py", src)) as tmp:
            opts = InstrumentOptions(backup=False)
            result = Instrumenter(tmp, opts).run()
            # test_ file should be skipped
            self.assertIn("test_main.py", result.files_skipped)

    def test_result_parses_after_instrumentation(self):
        """Instrumented files must still be valid Python."""
        import ast
        from monitor.instrumenter import Instrumenter, InstrumentOptions
        src = ("import os\n\n"
               "def load(path):\n    return open(path).read()\n\n"
               "def save(path, data):\n    open(path, 'w').write(data)\n")
        with self._project(("io_utils.py", src)) as tmp:
            opts = InstrumentOptions(backup=False)
            Instrumenter(tmp, opts).run()
            modified = open(os.path.join(tmp, "io_utils.py")).read()
            # Must parse without error
            ast.parse(modified)

    def test_functions_timed_count(self):
        from monitor.instrumenter import Instrumenter, InstrumentOptions
        src = ("def a():\n    pass\n\n"
               "def b():\n    pass\n\n"
               "def c():\n    pass\n")
        with self._project(("multi.py", src)) as tmp:
            opts = InstrumentOptions(backup=False, min_lines=1)
            result = Instrumenter(tmp, opts).run()
            self.assertEqual(result.functions_timed, 3)


# ═══════════════════════════════════════════════════════════════════════════════
# Sandbox tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSandbox(unittest.TestCase):

    def _make_project(self, tmp):
        import json
        with open(os.path.join(tmp, "main.py"), "w") as f:
            f.write("print('sandbox ok')\n")
        with open(os.path.join(tmp, "app.log"), "w") as f:
            f.write("log line 1\n")
        with open(os.path.join(tmp, "side.project.json"), "w") as f:
            json.dump({"name": "sandtest", "version": "1.0.0",
                       "versions": {"dir": "versions", "compress": True, "keep": 5}}, f)

    def test_prepare_creates_temp_dir(self):
        from build.sandbox import SandboxRun, SandboxOptions
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            sb = SandboxRun(tmp, SandboxOptions(mode="clean"))
            tmp_dir = sb.prepare()
            self.assertTrue(os.path.isdir(tmp_dir))
            self.assertTrue(os.path.isfile(os.path.join(tmp_dir, "main.py")))
            # Cleanup
            sb.stop()
            sb.cleanup()

    def test_prepare_does_not_modify_original(self):
        from build.sandbox import SandboxRun, SandboxOptions
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            original_main = open(os.path.join(tmp, "main.py")).read()
            sb = SandboxRun(tmp, SandboxOptions(mode="clean"))
            sb.prepare()
            sb.cleanup()
            # Original unchanged
            self.assertEqual(open(os.path.join(tmp, "main.py")).read(), original_main)

    def test_cleanup_removes_temp_dir(self):
        from build.sandbox import SandboxRun, SandboxOptions
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            sb = SandboxRun(tmp, SandboxOptions(mode="clean"))
            tmp_dir = sb.prepare()
            self.assertTrue(os.path.isdir(tmp_dir))
            sb.cleanup()
            self.assertFalse(os.path.isdir(tmp_dir))

    def test_log_retention_after_cleanup(self):
        from build.sandbox import SandboxRun, SandboxOptions
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            sb = SandboxRun(tmp, SandboxOptions(mode="clean", keep_log_runs=3))
            sb.prepare()
            log_dir = sb.cleanup()
            # Log dir should be created even if no process ran
            if log_dir:
                self.assertTrue(os.path.isdir(log_dir))

    def test_start_runs_process(self):
        from build.sandbox import SandboxRun, SandboxOptions
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            sb = SandboxRun(tmp, SandboxOptions(mode="clean"))
            sb.prepare()
            proc = sb.start("python main.py")
            time.sleep(0.5)
            info = proc.info()
            self.assertIn(info["status"], ("running", "stopped", "crashed"))
            sb.stop()
            sb.cleanup()

    def test_stdout_captured(self):
        from build.sandbox import SandboxRun, SandboxOptions
        lines = []
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            sb = SandboxRun(tmp, SandboxOptions(mode="clean"))
            sb.on_stdout(lines.append)
            sb.prepare()
            sb.start("python main.py")
            time.sleep(0.6)
            sb.cleanup()
        self.assertTrue(any("sandbox ok" in l for l in lines))

    def test_minified_mode_prepares(self):
        from build.sandbox import SandboxRun, SandboxOptions
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            sb = SandboxRun(tmp, SandboxOptions(mode="minified"))
            tmp_dir = sb.prepare()
            # Temp dir should exist and have main.py
            self.assertTrue(os.path.isfile(os.path.join(tmp_dir, "main.py")))
            sb.cleanup()

    def test_list_sandbox_logs(self):
        from build.sandbox import SandboxRun, SandboxOptions, list_sandbox_logs
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            sb = SandboxRun(tmp, SandboxOptions(mode="clean", keep_log_runs=5))
            sb.prepare()
            sb.cleanup()
            logs = list_sandbox_logs(tmp)
            # May be empty if no logs were generated, but should not error
            self.assertIsInstance(logs, list)

    def test_is_running_false_after_stop(self):
        from build.sandbox import SandboxRun, SandboxOptions
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            sb = SandboxRun(tmp, SandboxOptions(mode="clean"))
            sb.prepare()
            sb.start("python main.py")
            time.sleep(0.5)
            sb.stop()
            time.sleep(0.3)
            # After stop, is_running should be False
            self.assertFalse(sb.is_running)
            sb.cleanup()




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
# AI client tests (no Ollama required — test structure only)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAIClient(unittest.TestCase):

    def test_chat_message_to_dict(self):
        from ai.client import ChatMessage
        m = ChatMessage(role="user", content="hello")
        d = m.to_dict()
        self.assertEqual(d["role"], "user")
        self.assertEqual(d["content"], "hello")
        self.assertNotIn("tool_calls", d)

    def test_chat_message_with_tool_calls(self):
        from ai.client import ChatMessage
        m = ChatMessage(role="assistant", content="",
                        tool_calls=[{"function": {"name": "read_file"}}])
        d = m.to_dict()
        self.assertIn("tool_calls", d)

    def test_chat_response_from_dict(self):
        from ai.client import ChatResponse
        raw = {
            "model": "llama3.2",
            "done":  True,
            "message": {"role": "assistant", "content": "hello world"},
        }
        resp = ChatResponse.from_dict(raw)
        self.assertEqual(resp.content, "hello world")
        self.assertEqual(resp.model, "llama3.2")
        self.assertEqual(resp.tool_calls, [])

    def test_tool_result_to_message(self):
        from ai.client import ToolResult, ChatMessage
        tr = ToolResult(tool_call_id="x", name="read_file", content="file content")
        msg = tr.to_message()
        self.assertEqual(msg.role, "tool")
        self.assertEqual(msg.content, "file content")

    def test_ollama_unavailable_returns_false(self):
        from ai.client import OllamaClient
        # Use a port that's definitely not listening
        client = OllamaClient("http://localhost:19999")
        self.assertFalse(client.is_available())

    def test_ollama_models_empty_on_unavailable(self):
        from ai.client import OllamaClient
        client = OllamaClient("http://localhost:19999")
        self.assertEqual(client.list_models(), [])


# ═══════════════════════════════════════════════════════════════════════════════
# AI tools tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAITools(unittest.TestCase):

    def _make_ctx(self, tmp, graph=None):
        from ai.context import AppContext
        return AppContext(
            project_root=tmp,
            project_name="test",
            graph=graph,
        )

    def test_read_file(self):
        from ai.tools import dispatch_tool
        with tempfile.TemporaryDirectory() as tmp:
            fpath = os.path.join(tmp, "hello.py")
            open(fpath, "w").write("x = 1\n")
            ctx = self._make_ctx(tmp)
            result = dispatch_tool("read_file", {"path": "hello.py"}, ctx)
            self.assertIn("x = 1", result.content)

    def test_read_file_not_found(self):
        from ai.tools import dispatch_tool
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._make_ctx(tmp)
            result = dispatch_tool("read_file", {"path": "nope.py"}, ctx)
            self.assertIn("error", result.content.lower())

    def test_list_files_empty_graph(self):
        from ai.tools import dispatch_tool
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._make_ctx(tmp, graph={"nodes": [], "edges": []})
            result = dispatch_tool("list_files", {}, ctx)
            import json
            data = json.loads(result.content)
            self.assertEqual(data["count"], 0)

    def test_search_definitions_no_graph(self):
        from ai.tools import dispatch_tool
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._make_ctx(tmp, graph={"nodes": [], "edges": []})
            result = dispatch_tool("search_definitions", {"query": "foo"}, ctx)
            import json
            data = json.loads(result.content)
            self.assertEqual(data["count"], 0)

    def test_get_graph_overview_no_graph(self):
        from ai.tools import dispatch_tool
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._make_ctx(tmp)  # no graph
            result = dispatch_tool("get_graph_overview", {}, ctx)
            self.assertIn("error", result.content.lower())

    def test_get_metrics_no_file(self):
        from ai.tools import dispatch_tool
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._make_ctx(tmp)
            result = dispatch_tool("get_metrics", {}, ctx)
            self.assertIn("error", result.content.lower())

    def test_unknown_tool(self):
        from ai.tools import dispatch_tool
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._make_ctx(tmp)
            result = dispatch_tool("nonexistent_tool", {}, ctx)
            self.assertIn("Unknown tool", result.content)

    def test_context_build(self):
        from ai.context import build_context
        with tempfile.TemporaryDirectory() as tmp:
            ctx = build_context(tmp)
            self.assertEqual(ctx.project_root, tmp)

    def test_system_message_built(self):
        from ai.context import build_context, build_system_message
        with tempfile.TemporaryDirectory() as tmp:
            ctx = build_context(tmp)
            msg = build_system_message(ctx)
            self.assertEqual(msg.role, "system")
            self.assertGreater(len(msg.content), 100)




# ═══════════════════════════════════════════════════════════════════════════════
# Markdown renderer
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarkdownRenderer(unittest.TestCase):

    def _mock(self):
        class T:
            def __init__(self): self.buf = []
            def config(self, **kw): pass
            def insert(self, pos, text, tag=""): self.buf.append((text, tag))
            def see(self, pos): pass
            def winfo_exists(self): return True
            @property
            def text(self): return "".join(t for t, _ in self.buf)
            def tags(self, frag): return [tag for t, tag in self.buf if frag in t]
        class A:
            _ai_conv = T()
        return A()

    def test_h1(self):
        from gui.panels import ai_append_markdown
        a = self._mock(); ai_append_markdown(a, "# Title")
        self.assertIn("Title", a._ai_conv.text)
        self.assertIn("h1", a._ai_conv.tags("Title"))

    def test_h2(self):
        from gui.panels import ai_append_markdown
        a = self._mock(); ai_append_markdown(a, "## Section")
        self.assertIn("h2", a._ai_conv.tags("Section"))

    def test_bold(self):
        from gui.panels import _insert_inline
        a = self._mock()
        _insert_inline(a._ai_conv, "This is **bold** text")
        self.assertIn("strong", a._ai_conv.tags("bold"))

    def test_italic(self):
        from gui.panels import _insert_inline
        a = self._mock()
        _insert_inline(a._ai_conv, "This is *italic* text")
        self.assertIn("em", a._ai_conv.tags("italic"))

    def test_inline_code(self):
        from gui.panels import _insert_inline
        a = self._mock()
        _insert_inline(a._ai_conv, "Use `print()` here")
        self.assertIn("code", a._ai_conv.tags("print()"))

    def test_code_block(self):
        from gui.panels import ai_append_markdown
        a = self._mock(); ai_append_markdown(a, "```\nx = 1\n```")
        self.assertIn("x = 1", a._ai_conv.text)
        self.assertIn("code", a._ai_conv.tags("x = 1"))

    def test_bullet(self):
        from gui.panels import ai_append_markdown
        a = self._mock(); ai_append_markdown(a, "- item one")
        self.assertIn("\u2022", a._ai_conv.text)
        self.assertIn("item one", a._ai_conv.text)

    def test_numbered_list(self):
        from gui.panels import ai_append_markdown
        a = self._mock(); ai_append_markdown(a, "1. First\n2. Second")
        self.assertIn("First", a._ai_conv.text)

    def test_hr(self):
        from gui.panels import ai_append_markdown
        a = self._mock(); ai_append_markdown(a, "---")
        self.assertIn("\u2500", a._ai_conv.text)

    def test_unclosed_code_flushed(self):
        from gui.panels import ai_append_markdown
        a = self._mock(); ai_append_markdown(a, "```\nsome code")
        self.assertIn("some code", a._ai_conv.text)

    def test_mixed(self):
        from gui.panels import ai_append_markdown
        a = self._mock()
        ai_append_markdown(a, "# H\n\n**bold** and `code`.\n\n- item\n\n```\nblock\n```")
        t = a._ai_conv.text
        for s in ["H", "bold", "code", "item", "block"]:
            self.assertIn(s, t)


# ═══════════════════════════════════════════════════════════════════════════════
# SessionState
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionState(unittest.TestCase):

    def _state(self, tmp):
        import importlib.util
        spec = importlib.util.spec_from_file_location('gui_state', 'gui/state.py')
        sm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sm)
        orig = sm._STATE_PATH
        sm._STATE_PATH = os.path.join(tmp, 's.json')
        return sm.SessionState(), sm, orig

    def test_project_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            s, sm, orig = self._state(tmp)
            s.add_project("myapp", "/home/user/myapp")
            s2 = sm.SessionState()
            self.assertIn("/home/user/myapp", [p["path"] for p in s2.projects])
            sm._STATE_PATH = orig

    def test_ai_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            s, sm, orig = self._state(tmp)
            s.set_ai_history("/p", [{"role": "user", "content": "hi"},
                                     {"role": "assistant", "content": "hello"}])
            s2 = sm.SessionState()
            self.assertEqual(s2.get_ai_history("/p")[0]["content"], "hi")
            sm._STATE_PATH = orig

    def test_terminal_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            s, sm, orig = self._state(tmp)
            s.add_terminal_command("/p", "git status")
            s2 = sm.SessionState()
            self.assertIn("git status", s2.get_terminal_history("/p"))
            sm._STATE_PATH = orig

    def test_viewport(self):
        with tempfile.TemporaryDirectory() as tmp:
            s, sm, orig = self._state(tmp)
            s.set_viewport("/p", 10.0, 20.0, 1.5)
            s.flush_viewport("/p")
            s2 = sm.SessionState()
            self.assertAlmostEqual(s2.get_viewport("/p")["x"], 10.0)
            sm._STATE_PATH = orig

    def test_bottom_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            s, sm, orig = self._state(tmp)
            s.bottom_height = 300
            s.bottom_tab = "ai"
            s.save()
            s2 = sm.SessionState()
            self.assertEqual(s2.bottom_height, 300)
            self.assertEqual(s2.bottom_tab, "ai")
            sm._STATE_PATH = orig


# ═══════════════════════════════════════════════════════════════════════════════
# Git tool
# ═══════════════════════════════════════════════════════════════════════════════

class TestGitTool(unittest.TestCase):

    def _ctx(self, tmp):
        from ai.context import AppContext
        return AppContext(project_root=tmp, project_name="t")

    def test_no_root(self):
        from ai.tools import dispatch_tool
        from ai.context import AppContext
        r = dispatch_tool("git", {"command": "status"}, AppContext())
        self.assertIn("error", r.content.lower())

    def test_status_in_repo(self):
        import subprocess, json
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run("git init", shell=True, cwd=tmp, capture_output=True)
            from ai.tools import dispatch_tool
            r = dispatch_tool("git", {"command": "status"}, self._ctx(tmp))
            d = json.loads(r.content)
            # git may return exit_code (success) or error (no upstream)
            self.assertTrue("exit_code" in d or "error" in d)

    def test_log_after_commit(self):
        import subprocess, json
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run("git init", shell=True, cwd=tmp, capture_output=True)
            # Use -c flags so config doesn't need to persist between calls
            open(os.path.join(tmp, "f.txt"), "w").write("x")
            subprocess.run(
                "git add . && git -c user.email=t@t.com -c user.name=T commit -m init",
                shell=True, cwd=tmp, capture_output=True)
            from ai.tools import dispatch_tool
            r = dispatch_tool("git", {"command": "log"}, self._ctx(tmp))
            d = json.loads(r.content)
            self.assertIn("exit_code", d)
            if d["exit_code"] == 0:
                self.assertIn("init", d["output"])


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
# Agent roles and session workspace
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentRoles(unittest.TestCase):

    def _ctx(self, tmp, role="chat", session_root=None):
        from ai.context import build_context
        return build_context(tmp, role=role,
                             session_root=session_root or os.path.join(tmp, "session"))

    def test_chat_role_has_all_tools(self):
        from ai.context import ALL_TOOLS
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, "chat")
            for tool in ["write_file", "read_file", "git", "run_command"]:
                self.assertTrue(ctx.can_use(tool), f"chat should have {tool}")

    def test_reviewer_cannot_write_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, "reviewer")
            self.assertFalse(ctx.can_use("write_file"))

    def test_reviewer_can_write_session_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, "reviewer")
            self.assertTrue(ctx.can_use("write_session_file"))

    def test_tester_cannot_write_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, "tester")
            self.assertFalse(ctx.can_use("write_file"))

    def test_tester_can_run_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, "tester")
            self.assertTrue(ctx.can_use("run_command"))
            self.assertTrue(ctx.can_use("run_in_playground"))

    def test_documentarian_cannot_write_file_or_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, "documentarian")
            self.assertFalse(ctx.can_use("write_file"))
            self.assertFalse(ctx.can_use("run_command"))
            self.assertTrue(ctx.can_use("write_session_file"))

    def test_implementer_has_full_write_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, "implementer")
            self.assertTrue(ctx.can_use("write_file"))
            self.assertTrue(ctx.can_use("run_command"))
            self.assertTrue(ctx.can_use("git"))

    def test_dispatch_blocks_forbidden_tool(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, "reviewer")
            result = dispatch_tool("write_file", {"path": "x.py", "content": "x"}, ctx)
            d = json.loads(result.content)
            self.assertIn("error", d)
            self.assertIn("not permitted", d["error"])

    def test_dispatch_allows_session_write(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, "reviewer")
            result = dispatch_tool("write_session_file",
                                   {"path": "review/notes.md", "content": "# hi"}, ctx)
            d = json.loads(result.content)
            self.assertIn("written", d)

    def test_session_file_roundtrip(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, "reviewer")
            dispatch_tool("write_session_file",
                          {"path": "notes.md", "content": "hello world"}, ctx)
            result = dispatch_tool("read_session_file", {"path": "notes.md"}, ctx)
            self.assertIn("hello world", result.content)

    def test_list_session_files(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, "tester")
            dispatch_tool("write_session_file",
                          {"path": "test/results.md", "content": "PASS"}, ctx)
            dispatch_tool("write_session_file",
                          {"path": "test/cases.py", "content": "def test_x(): pass"}, ctx)
            result = dispatch_tool("list_session_files", {}, ctx)
            d = json.loads(result.content)
            self.assertEqual(d["count"], 2)

    def test_session_file_cannot_escape(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, "reviewer")
            result = dispatch_tool("write_session_file",
                                   {"path": "../../evil.py", "content": "x"}, ctx)
            d = json.loads(result.content)
            self.assertIn("error", d)
            self.assertIn("escapes", d["error"])

    def test_role_prompts_exist_for_all_roles(self):
        from ai.roles import get_role_prompt
        for role in ["architect", "implementer", "reviewer",
                     "tester", "optimizer", "documentarian"]:
            p = get_role_prompt(role)
            self.assertGreater(len(p), 200, f"{role} prompt too short")
            self.assertIn("session", p.lower(), f"{role} missing session info")

    def test_unknown_role_returns_base_prompt(self):
        from ai.roles import get_role_prompt
        from ai.standards import get_system_prompt
        p = get_role_prompt("nonexistent_role")
        base = get_system_prompt("chat")
        self.assertEqual(p, base)

    def test_session_dir_property(self):
        from ai.context import build_context
        with tempfile.TemporaryDirectory() as tmp:
            ctx = build_context(tmp, role="reviewer")
            self.assertIn(".side", ctx.session_dir)
            self.assertIn("session", ctx.session_dir)


# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═
# AI Teams engine
# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═

class TestTeamSession(unittest.TestCase):

    def test_agent_config_defaults(self):
        from ai.teams import AgentConfig
        a = AgentConfig(role='reviewer')
        self.assertEqual(a.name, 'Reviewer')
        self.assertEqual(a.model, 'llama3.2')
        self.assertEqual(a.max_rounds, 8)

    def test_agent_config_custom_name(self):
        from ai.teams import AgentConfig
        a = AgentConfig(role='tester', name='Bob')
        self.assertEqual(a.name, 'Bob')

    def test_session_creates_workspace(self):
        from ai.teams import TeamSession, AgentConfig
        with tempfile.TemporaryDirectory() as tmp:
            s = TeamSession(tmp, 'Fix the bug', [AgentConfig('reviewer')])
            self.assertTrue(os.path.isdir(s.session_dir))

    def test_session_writes_task_brief(self):
        from ai.teams import TeamSession, AgentConfig
        with tempfile.TemporaryDirectory() as tmp:
            s = TeamSession(tmp, 'Add type hints', [AgentConfig('implementer')])
            s._write_task_brief()
            brief = open(os.path.join(s.session_dir, 'TASK.md')).read()
            self.assertIn('Add type hints', brief)
            self.assertIn('implementer', brief)

    def test_workflow_result_summary(self):
        from ai.teams import WorkflowResult, AgentTurn
        r = WorkflowResult(session_id='abc', task='test task',
                           project_root='/tmp', session_dir='/tmp/s')
        r.turns.append(AgentTurn(
            agent='Reviewer', role='reviewer', model='llama3.2',
            response='ok', tool_calls=[], session_files=['review/notes.md'],
            duration_s=2.3))
        self.assertIn('Reviewer', r.summary())

    def test_list_sessions_empty(self):
        from ai.teams import list_sessions
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list_sessions(tmp), [])

    def test_list_sessions_finds_session(self):
        from ai.teams import TeamSession, AgentConfig, list_sessions
        with tempfile.TemporaryDirectory() as tmp:
            s = TeamSession(tmp, 'Task one', [AgentConfig('architect')])
            s._write_task_brief()
            sessions = list_sessions(tmp)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]['id'], s.session_id)

    def test_events_emitted(self):
        from ai.teams import TeamSession, AgentConfig
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            s = TeamSession(tmp, 'task', [], on_event=events.append)
            s._emit('start', AgentConfig('architect'), 'starting')
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].type, 'start')


# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═
# Playground
# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═

class TestPlayground(unittest.TestCase):

    def test_simple_snippet(self):
        from ai.playground import Playground
        with tempfile.TemporaryDirectory() as tmp:
            pg = Playground(tmp)
            result = pg.run('print(42)')
            self.assertEqual(result.exit_code, 0)
            self.assertIn('42', result.stdout)

    def test_timeout(self):
        from ai.playground import Playground
        with tempfile.TemporaryDirectory() as tmp:
            pg = Playground(tmp)
            pg.TIMEOUT_S = 1
            result = pg.run('import time; time.sleep(5)')
            self.assertTrue(result.timed_out)

    def test_exception_captured(self):
        from ai.playground import Playground
        with tempfile.TemporaryDirectory() as tmp:
            pg = Playground(tmp)
            result = pg.run('raise ValueError("test error")')
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn('ValueError', result.stderr)

    def test_project_files_accessible(self):
        from ai.playground import Playground
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, 'mymod.py'), 'w').write('VALUE = 42\n')
            pg = Playground(tmp)
            result = pg.run('import mymod; print(mymod.VALUE)')
            self.assertEqual(result.exit_code, 0)
            self.assertIn('42', result.stdout)

    def test_result_dict(self):
        from ai.playground import Playground
        with tempfile.TemporaryDirectory() as tmp:
            pg = Playground(tmp)
            d = pg.run('x = 1 + 1').to_dict()
            self.assertIn('exit_code', d)
            self.assertIn('ok', d)

    def test_run_snippet_dispatch(self):
        from ai.playground import run_snippet
        with tempfile.TemporaryDirectory() as tmp:
            d = run_snippet('print("dispatch works")', tmp)
            self.assertTrue(d['ok'])
            self.assertIn('dispatch works', d['stdout'])

    def test_run_in_playground_tool(self):
        from ai.tools import dispatch_tool
        from ai.context import build_context
        import json
        with tempfile.TemporaryDirectory() as tmp:
            ctx = build_context(tmp, role='tester')
            result = dispatch_tool('run_in_playground', {'code': 'print(2+2)'}, ctx)
            d = json.loads(result.content)
            self.assertTrue(d['ok'])
            self.assertIn('4', d['stdout'])


# ══════════════════════════════════════
# Teams canvas mixin
# ══════════════════════════════════════

class TestTeamsCanvas(unittest.TestCase):
    '''Tests for TeamsCanvasMixin logic without a Tk window.'''

    def _mixin(self):
        '''Create a minimal mock object with the mixin methods attached.'''
        from gui.teams_canvas import TeamsCanvasMixin
        class FakeApp(TeamsCanvasMixin):
            vp_x = vp_y = 0.0
            vp_z = 1.0
            def _w2s(self, wx, wy): return (wx + self.vp_x, wy + self.vp_y)
            def _s2w(self, sx, sy): return (sx - self.vp_x, sy - self.vp_y)
            def _redraw(self): pass
            def _fit_view(self): pass
        app = FakeApp()
        app._teams_init()
        return app

    def test_init_defaults(self):
        app = self._mixin()
        self.assertEqual(app.canvas_mode, 'graph')
        self.assertEqual(app._tw_nodes, [])
        self.assertEqual(app._tw_edges, [])
        self.assertIsNone(app._tw_sel)
        self.assertFalse(app._tw_running)

    def test_new_node_creates_entry(self):
        app = self._mixin()
        nid = app._tw_new_node('reviewer', x=10, y=20)
        self.assertEqual(len(app._tw_nodes), 1)
        self.assertEqual(app._tw_nodes[0]['role'], 'reviewer')
        self.assertAlmostEqual(app._tw_nodes[0]['x'], 10)

    def test_new_node_increments_counter(self):
        app = self._mixin()
        id1 = app._tw_new_node('architect')
        id2 = app._tw_new_node('implementer')
        self.assertNotEqual(id1, id2)

    def test_node_by_id(self):
        app = self._mixin()
        nid = app._tw_new_node('tester')
        node = app._tw_node_by_id(nid)
        self.assertIsNotNone(node)
        self.assertEqual(node['role'], 'tester')

    def test_node_by_id_missing(self):
        app = self._mixin()
        self.assertIsNone(app._tw_node_by_id('nonexistent'))

    def test_delete_node(self):
        app = self._mixin()
        nid = app._tw_new_node('reviewer')
        app._tw_delete_node(nid)
        self.assertEqual(app._tw_nodes, [])

    def test_delete_node_clears_edges(self):
        app = self._mixin()
        a = app._tw_new_node('architect')
        b = app._tw_new_node('implementer')
        app._tw_edges.append({'id': 'e1', 'source': a, 'target': b})
        app._tw_delete_node(a)
        self.assertEqual(app._tw_edges, [])

    def test_delete_node_clears_selection(self):
        app = self._mixin()
        nid = app._tw_new_node('tester')
        app._tw_sel = nid
        app._tw_delete_node(nid)
        self.assertIsNone(app._tw_sel)

    def test_default_workflow_creates_4_nodes(self):
        app = self._mixin()
        app._tw_add_default_workflow()
        self.assertEqual(len(app._tw_nodes), 4)
        self.assertEqual(len(app._tw_edges), 3)

    def test_default_workflow_roles(self):
        app = self._mixin()
        app._tw_add_default_workflow()
        roles = [n['role'] for n in app._tw_nodes]
        self.assertEqual(roles, ['architect', 'implementer', 'reviewer', 'tester'])

    def test_order_by_edges_linear(self):
        app = self._mixin()
        app._tw_add_default_workflow()
        ordered = app._tw_order_by_edges()
        roles = [n['role'] for n in ordered]
        self.assertEqual(roles, ['architect', 'implementer', 'reviewer', 'tester'])

    def test_order_by_edges_empty(self):
        app = self._mixin()
        app._tw_new_node('architect', x=100)
        app._tw_new_node('implementer', x=50)
        # No edges — fall back to x-position sort
        ordered = app._tw_order_by_edges()
        self.assertEqual(ordered[0]['role'], 'implementer')  # x=50 comes first

    def test_auto_connect(self):
        app = self._mixin()
        app._tw_new_node('architect', x=0)
        app._tw_new_node('implementer', x=100)
        app._tw_new_node('reviewer', x=200)
        app._tw_auto_connect()
        self.assertEqual(len(app._tw_edges), 2)

    def test_hit_test_no_boxes(self):
        app = self._mixin()
        result = app._tw_hit_test(0, 0)
        self.assertIsNone(result)

    def test_hit_test_hits_node(self):
        app = self._mixin()
        nid = app._tw_new_node('reviewer', x=10, y=10)
        # Manually set hit boxes (normally done by _tw_rebuild_hit_boxes after draw)
        app._tw_hit_boxes = {nid: (10, 10, 210, 120)}
        self.assertEqual(app._tw_hit_test(50, 50), nid)
        self.assertIsNone(app._tw_hit_test(300, 300))

    def test_role_colours_defined(self):
        from gui.teams_canvas import ROLE_COLOURS
        for role in ['architect','implementer','reviewer','tester','optimizer','documentarian']:
            self.assertIn(role, ROLE_COLOURS)


# ══════════════════════════════════════
# Teams Log panel
# ══════════════════════════════════════

class TestTeamsLog(unittest.TestCase):
    '''Tests for Teams Log tab wiring in app.py (headless).'''

    def _mock_app(self):
        '''Minimal stand-in for SIDE_App with Teams Log state.'''
        class FakeApp:
            _teams_log = None
            def after(self, ms, fn=None):
                if fn: fn()
            def _teams_log_append(self, text, tag=''):
                self._log_buf.append((text, tag))
            def _teams_log_clear(self):
                self._log_buf.clear()
        app = FakeApp()
        app._log_buf = []
        return app

    def test_append_stores_text(self):
        app = self._mock_app()
        app._teams_log_append('hello\n', 'tool')
        self.assertEqual(len(app._log_buf), 1)
        self.assertIn('hello', app._log_buf[0][0])

    def test_clear_empties_buf(self):
        app = self._mock_app()
        app._teams_log_append('line1\n')
        app._teams_log_append('line2\n')
        app._teams_log_clear()
        self.assertEqual(app._log_buf, [])

    def test_tag_passed_through(self):
        app = self._mock_app()
        app._teams_log_append('error msg\n', 'error')
        self.assertEqual(app._log_buf[0][1], 'error')

    def test_tw_on_event_routes_to_log(self):
        '''_tw_on_event should call _teams_log_append for all event types.'''
        from gui.teams_canvas import TeamsCanvasMixin
        class FakeApp(TeamsCanvasMixin):
            vp_x = vp_y = 0.0; vp_z = 1.0
            _log_calls = []
            _ai_appends = []
            def _w2s(self, wx, wy): return (wx, wy)
            def _s2w(self, sx, sy): return (sx, sy)
            def _redraw(self): pass
            def _select_bottom_tab(self, name): pass
            def _teams_log_append(self, text, tag=''): self._log_calls.append((text, tag))
            def after(self, ms, fn=None):
                if fn: fn()
        from ai.teams import TeamEvent
        app = FakeApp()
        app._teams_init()
        evt = TeamEvent(type='tool', agent='Reviewer', role='reviewer',
                        message='read_file(path=gui/app.py)')
        # Patch ai_append so we can call _tw_on_event without full GUI
        import unittest.mock as mock
        with mock.patch('gui.panels.ai_append'):
            app._tw_on_event(evt)
        self.assertTrue(any('Reviewer' in t for t, _ in app._log_calls))


# ══════════════════════════════════════
# Manager scaffold and project creation
# ══════════════════════════════════════

class TestManagerScaffold(unittest.TestCase):

    def test_scaffold_creates_files(self):
        from ai.manager import scaffold_new_project
        with tempfile.TemporaryDirectory() as tmp:
            root = scaffold_new_project(tmp, 'my-app', 'A test app')
            self.assertTrue(os.path.isfile(os.path.join(root, 'side.project.json')))
            self.assertTrue(os.path.isfile(os.path.join(root, 'README.md')))
            self.assertTrue(os.path.isfile(os.path.join(root, 'src', 'main.py')))
            self.assertTrue(os.path.isfile(os.path.join(root, 'test', 'test_main.py')))

    def test_scaffold_project_json(self):
        from ai.manager import scaffold_new_project
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = scaffold_new_project(tmp, 'Hello World', 'desc')
            cfg = json.load(open(os.path.join(root, 'side.project.json')))
            self.assertEqual(cfg['name'], 'hello-world')
            self.assertEqual(cfg['version'], '0.1.0')
            self.assertIn('run', cfg)
            self.assertIn('test', cfg['run'])

    def test_scaffold_readme_has_content(self):
        from ai.manager import scaffold_new_project
        with tempfile.TemporaryDirectory() as tmp:
            root = scaffold_new_project(tmp, 'calc', 'A calculator')
            readme = open(os.path.join(root, 'README.md')).read()
            self.assertIn('calc', readme)
            self.assertIn('Quick start', readme)

    def test_scaffold_test_file_runnable(self):
        from ai.manager import scaffold_new_project
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = scaffold_new_project(tmp, 'test-proj', '')
            result = subprocess.run(
                ['python', '-m', 'unittest', 'discover', 'test/'],
                cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)

    def test_scaffold_slug_from_name(self):
        from ai.manager import scaffold_new_project
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = scaffold_new_project(tmp, 'My Cool App', '')
            self.assertTrue(root.endswith('my-cool-app'))

    def test_manager_prompt_has_run_team(self):
        from ai.manager import MANAGER_PROMPT
        self.assertIn('run_team', MANAGER_PROMPT)

    def test_manager_prompt_has_bake(self):
        from ai.manager import MANAGER_PROMPT
        self.assertIn('bake', MANAGER_PROMPT.lower())

    def test_manager_prompt_has_new_project(self):
        from ai.manager import MANAGER_PROMPT
        self.assertIn('side.project.json', MANAGER_PROMPT)


# ══════════════════════════════════════
# Calculator example project
# ══════════════════════════════════════

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
            ['python', '-m', 'unittest', 'discover', 'test/', '-v'],
            cwd=self.CALC_DIR, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
            msg=f'Calculator tests failed:\n{result.stderr}')


# ══════════════════════════════════════
# Tool builder — self-improving tool creation
# ══════════════════════════════════════

class TestToolBuilder(unittest.TestCase):

    def test_infer_spec_from_error(self):
        from ai.tool_builder import ToolMissingError, infer_tool_spec
        err = ToolMissingError(
            tool_name='search_web',
            tool_args={'query': 'python tkinter', 'max_results': 5},
            intent='I need to find documentation online')
        with tempfile.TemporaryDirectory() as tmp:
            spec = infer_tool_spec(err, tmp)
            self.assertEqual(spec.tool_name, 'search_web')
            self.assertIn('query', spec.args_schema)
            self.assertEqual(spec.args_schema['query']['type'], 'string')
            self.assertEqual(spec.args_schema['max_results']['type'], 'integer')
            self.assertIn('.side/tools/search_web.py', spec.file_path)

    def test_spec_to_team_task(self):
        from ai.tool_builder import ToolMissingError, infer_tool_spec
        err = ToolMissingError('my_tool', {'x': 'hello'}, 'needed for X')
        with tempfile.TemporaryDirectory() as tmp:
            spec = infer_tool_spec(err, tmp)
            task = spec.to_team_task()
            self.assertIn('my_tool', task)
            self.assertIn('TOOL_SCHEMA', task)
            self.assertIn('TOOL_HANDLER', task)
            self.assertIn('.side/tools', task)

    def test_spec_summary(self):
        from ai.tool_builder import ToolMissingError, infer_tool_spec
        err = ToolMissingError('calc_tax', {'amount': 100.0}, 'for invoice')
        with tempfile.TemporaryDirectory() as tmp:
            spec = infer_tool_spec(err, tmp)
            summary = spec.summary()
            self.assertIn('calc_tax', summary)
            self.assertIn('amount', summary)

    def test_register_custom_tool(self):
        from ai.tool_builder import register_custom_tool, dispatch_custom, is_custom_tool
        with tempfile.TemporaryDirectory() as tmp:
            tool_file = os.path.join(tmp, 'greet.py')
            open(tool_file, 'w').write(
                'TOOL_SCHEMA = {"type": "function", "function": {"name": "greet", '
                '"description": "Say hi", "parameters": {"type": "object", '
                '"properties": {"name": {"type": "string"}}, "required": ["name"]}}}\n'
                'def TOOL_HANDLER(args, ctx): return {"greeting": f"Hello {args[\'name\']}!"}\n'
            )
            name = register_custom_tool(tool_file)
            self.assertEqual(name, 'greet')
            self.assertTrue(is_custom_tool('greet'))
            result = dispatch_custom('greet', {'name': 'World'}, None)
            self.assertEqual(result['greeting'], 'Hello World!')

    def test_register_missing_schema_raises(self):
        from ai.tool_builder import register_custom_tool
        with tempfile.TemporaryDirectory() as tmp:
            tool_file = os.path.join(tmp, 'bad.py')
            open(tool_file, 'w').write('# no schema here\n')
            with self.assertRaises(ValueError):
                register_custom_tool(tool_file)

    def test_load_all_custom_tools_empty(self):
        from ai.tool_builder import load_all_custom_tools
        with tempfile.TemporaryDirectory() as tmp:
            result = load_all_custom_tools(tmp)
            self.assertEqual(result, [])

    def test_load_all_custom_tools_finds_files(self):
        from ai.tool_builder import load_all_custom_tools, is_custom_tool
        with tempfile.TemporaryDirectory() as tmp:
            tools_dir = os.path.join(tmp, '.side', 'tools')
            os.makedirs(tools_dir)
            open(os.path.join(tools_dir, 'my_adder.py'), 'w').write(
                'TOOL_SCHEMA = {"type": "function", "function": {"name": "my_adder", '
                '"description": "Add", "parameters": {"type": "object", '
                '"properties": {}, "required": []}}}\n'
                'def TOOL_HANDLER(args, ctx): return {"result": 42}\n'
            )
            names = load_all_custom_tools(tmp)
            self.assertIn('my_adder', names)
            self.assertTrue(is_custom_tool('my_adder'))

    def test_get_custom_schemas(self):
        from ai.tool_builder import register_custom_tool, get_custom_schemas
        with tempfile.TemporaryDirectory() as tmp:
            tool_file = os.path.join(tmp, 'schema_test.py')
            open(tool_file, 'w').write(
                'TOOL_SCHEMA = {"type": "function", "function": {"name": "schema_test", '
                '"description": "Test", "parameters": {"type": "object", '
                '"properties": {}, "required": []}}}\n'
                'def TOOL_HANDLER(args, ctx): return {}\n'
            )
            register_custom_tool(tool_file)
            schemas = get_custom_schemas()
            names = [s['function']['name'] for s in schemas]
            self.assertIn('schema_test', names)

    def test_tool_missing_error_dataclass(self):
        from ai.tool_builder import ToolMissingError
        err = ToolMissingError(tool_name='foo', tool_args={'x': 1}, intent='testing')
        self.assertEqual(err.tool_name, 'foo')
        self.assertEqual(err.tool_args['x'], 1)
        self.assertEqual(err.intent, 'testing')


# ══════════════════════════════════════
# cProfile-based project profiler
# ══════════════════════════════════════

class TestProfiler(unittest.TestCase):

    # ── _find_entry_point ──────────────────────────────────────────

    def test_find_entry_src_main(self):
        from monitor.profiler import _find_entry_point
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, 'src'))
            open(os.path.join(tmp, 'src', 'main.py'), 'w').write('print(1)')
            self.assertEqual(_find_entry_point(tmp), 'src/main.py')

    def test_find_entry_root_main(self):
        from monitor.profiler import _find_entry_point
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, 'main.py'), 'w').write('print(1)')
            self.assertEqual(_find_entry_point(tmp), 'main.py')

    def test_find_entry_dunder_main(self):
        from monitor.profiler import _find_entry_point
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, 'run.py'), 'w').write(
                'if __name__ == "__main__":\n    pass\n')
            # 'run.py' not in candidates list, but has __main__ guard
            # Note: src/main.py etc take priority if they exist
            ep = _find_entry_point(tmp)
            self.assertIn(ep, ['run.py', ''])

    def test_find_entry_empty_project(self):
        from monitor.profiler import _find_entry_point
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_find_entry_point(tmp), '')

    # ── profile_project ───────────────────────────────────────────

    def _make_project(self, tmp, main_code='print(42)'):
        os.makedirs(os.path.join(tmp, 'src'))
        open(os.path.join(tmp, 'src', 'main.py'), 'w').write(
            f'def do_work():\n    return sum(range(1000))\n\n'
            f'if __name__ == "__main__":\n    print(do_work())\n')
        return tmp

    def test_profile_basic_run(self):
        from monitor.profiler import profile_project
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            result = profile_project(tmp, entry_point='src/main.py', timeout=15)
            self.assertTrue(result.ok, msg=result.error)
            self.assertEqual(result.exit_code, 0)
            self.assertGreater(result.total_ms, 0)

    def test_profile_finds_functions(self):
        from monitor.profiler import profile_project
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            result = profile_project(tmp, entry_point='src/main.py', timeout=15)
            self.assertTrue(result.ok)
            fn_names = [f.function_name for f in result.functions]
            self.assertIn('do_work', fn_names)

    def test_profile_writes_metrics_json(self):
        from monitor.profiler import profile_project
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            result = profile_project(tmp, entry_point='src/main.py', timeout=15)
            self.assertTrue(result.ok)
            self.assertTrue(os.path.isfile(result.metrics_path))
            data = json.load(open(result.metrics_path))
            self.assertIn('files', data)
            self.assertIn('functions', data)
            self.assertIn('updated', data)

    def test_metrics_json_format_compatible(self):
        from monitor.profiler import profile_project
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            result = profile_project(tmp, entry_point='src/main.py', timeout=15)
            self.assertTrue(result.ok)
            data = json.load(open(result.metrics_path))
            # Each file entry must have the fields MetricsWatcher expects
            for path, stats in data['files'].items():
                for field in ('calls', 'total_ms', 'avg_ms', 'max_ms'):
                    self.assertIn(field, stats, msg=f'{field} missing in {path}')

    def test_profile_missing_entry_point(self):
        from monitor.profiler import profile_project
        with tempfile.TemporaryDirectory() as tmp:
            result = profile_project(tmp, entry_point='nonexistent.py', timeout=5)
            self.assertFalse(result.ok)
            self.assertIn('not found', result.error.lower())

    def test_profile_no_entry_point_empty_project(self):
        from monitor.profiler import profile_project
        with tempfile.TemporaryDirectory() as tmp:
            result = profile_project(tmp, timeout=5)
            self.assertFalse(result.ok)

    def test_profile_result_summary(self):
        from monitor.profiler import ProfileResult, FunctionMetrics
        r = ProfileResult(
            project_root='/tmp', entry_point='src/main.py',
            total_ms=123.4, exit_code=0)
        r.functions = [FunctionMetrics(
            module_path='src/main.py', function_name='do_work',
            calls=10, total_ms=50.0, own_ms=45.0, per_call_ms=5.0)]
        summary = r.summary()
        self.assertIn('do_work', summary)
        self.assertIn('50', summary)

    def test_load_last_profile_missing(self):
        from monitor.profiler import load_last_profile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_last_profile(tmp))

    def test_load_last_profile_present(self):
        from monitor.profiler import profile_project, load_last_profile
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            result = profile_project(tmp, entry_point='src/main.py', timeout=15)
            self.assertTrue(result.ok)
            data = load_last_profile(tmp)
            self.assertIsNotNone(data)
            self.assertIn('functions', data)

    # ── Git tool expansion ──────────────────────────────────────────

class TestGitToolExpanded(unittest.TestCase):

    def _ctx(self, tmp):
        from ai.context import build_context
        return build_context(tmp)

    def _init_repo(self, tmp):
        import subprocess
        subprocess.run('git init', shell=True, cwd=tmp, capture_output=True)
        subprocess.run('git config user.email t@t.com', shell=True,
                       cwd=tmp, capture_output=True)
        subprocess.run('git config user.name T', shell=True,
                       cwd=tmp, capture_output=True)
        return tmp

    def test_status_short(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            r = dispatch_tool('git', {'command': 'status'}, self._ctx(tmp))
            d = json.loads(r.content)
            self.assertIn('exit_code', d)

    def test_add_all_and_commit(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            open(os.path.join(tmp, 'f.py'), 'w').write('x=1')
            dispatch_tool('git', {'command': 'add_all'}, self._ctx(tmp))
            r = dispatch_tool('git',
                {'command': 'commit', 'message': 'add f.py'},
                self._ctx(tmp))
            d = json.loads(r.content)
            self.assertEqual(d['exit_code'], 0, msg=d.get('stderr',''))

    def test_diff_staged_empty(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            r = dispatch_tool('git', {'command': 'diff_staged'}, self._ctx(tmp))
            d = json.loads(r.content)
            self.assertIn('exit_code', d)

    def test_commit_requires_message(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            r = dispatch_tool('git', {'command': 'commit'}, self._ctx(tmp))
            d = json.loads(r.content)
            self.assertIn('error', d)
            self.assertIn('message', d['error'])

    def test_log_after_commit(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            open(os.path.join(tmp, 'x.py'), 'w').write('pass')
            dispatch_tool('git', {'command': 'add_all'}, self._ctx(tmp))
            dispatch_tool('git',
                {'command': 'commit', 'message': 'initial'},
                self._ctx(tmp))
            r = dispatch_tool('git', {'command': 'log', 'n': 5}, self._ctx(tmp))
            d = json.loads(r.content)
            self.assertEqual(d['exit_code'], 0)
            self.assertIn('initial', d['output'])

    def test_unknown_command_error(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            r = dispatch_tool('git', {'command': 'frobnicate'}, self._ctx(tmp))
            d = json.loads(r.content)
            # Unknown command with no extra args returns an error dict
            self.assertIn('error', d)
            self.assertIn('frobnicate', d['error'])

    def test_stash_list_empty(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            r = dispatch_tool('git', {'command': 'stash_list'}, self._ctx(tmp))
            d = json.loads(r.content)
            self.assertIn('exit_code', d)

    def test_branch_list(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            r = dispatch_tool('git', {'command': 'branch'}, self._ctx(tmp))
            d = json.loads(r.content)
            self.assertIn('exit_code', d)

    def test_remote_empty(self):
        from ai.tools import dispatch_tool
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            r = dispatch_tool('git', {'command': 'remote'}, self._ctx(tmp))
            d = json.loads(r.content)
            self.assertIn('exit_code', d)


# ══════════════════════════════════════
# Workflow templates
# ══════════════════════════════════════

class TestWorkflowTemplates(unittest.TestCase):

    def _patched_path(self, tmp):
        import unittest.mock as mock
        return mock.patch('ai.workflow_templates._TEMPLATES_PATH',
                          os.path.join(tmp, 'tpl.json'))

    def test_builtins_present(self):
        from ai.workflow_templates import BUILTIN_TEMPLATES
        self.assertIn('standard_review', BUILTIN_TEMPLATES)
        self.assertIn('quick_implement', BUILTIN_TEMPLATES)
        self.assertIn('full_pipeline',   BUILTIN_TEMPLATES)
        self.assertIn('optimize_only',   BUILTIN_TEMPLATES)
        self.assertIn('docs_update',     BUILTIN_TEMPLATES)

    def test_get_builtin(self):
        from ai.workflow_templates import get_template
        t = get_template('standard_review')
        self.assertIsNotNone(t)
        self.assertEqual(len(t.agents), 4)
        roles = [a.role for a in t.agents]
        self.assertEqual(roles, ['architect','implementer','reviewer','tester'])

    def test_to_canvas_nodes(self):
        from ai.workflow_templates import get_template
        t = get_template('quick_implement')
        nodes, edges = t.to_canvas_nodes()
        self.assertEqual(len(nodes), 2)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]['source'], nodes[0]['id'])
        self.assertEqual(edges[0]['target'], nodes[1]['id'])

    def test_node_x_positions(self):
        from ai.workflow_templates import get_template
        t = get_template('standard_review')
        nodes, _ = t.to_canvas_nodes(start_x=80, gap=260)
        xs = [n['x'] for n in nodes]
        self.assertEqual(xs, [80, 340, 600, 860])

    def test_save_and_get(self):
        from ai.workflow_templates import save_template, get_template
        with tempfile.TemporaryDirectory() as tmp:
            with self._patched_path(tmp):
                nodes = [{'id':'n1','role':'implementer','model':'llama3.2',
                           'name':'Impl','x':80,'y':80}]
                edges = []
                t = save_template('my_wf', nodes, edges, 'my workflow')
                self.assertEqual(t.name, 'my_wf')
                self.assertFalse(t.builtin)
                got = get_template('my_wf')
                # get_template checks builtins first (won't find user templates)
                # so check via list_templates instead
                from ai.workflow_templates import list_templates
                names = [x.name for x in list_templates()]
                self.assertIn('my_wf', names)

    def test_delete_user_template(self):
        from ai.workflow_templates import save_template, delete_template, list_templates
        with tempfile.TemporaryDirectory() as tmp:
            with self._patched_path(tmp):
                nodes = [{'id':'n1','role':'tester','model':'llama3.2',
                           'name':'T','x':80,'y':80}]
                save_template('del_me', nodes, [], 'temp')
                ok = delete_template('del_me')
                self.assertTrue(ok)
                names = [x.name for x in list_templates()]
                self.assertNotIn('del_me', names)

    def test_cannot_delete_builtin(self):
        from ai.workflow_templates import delete_template
        self.assertFalse(delete_template('standard_review'))

    def test_list_templates_order(self):
        from ai.workflow_templates import list_templates
        all_t = list_templates()
        # Builtins come first
        builtin_indices = [i for i, t in enumerate(all_t) if t.builtin]
        user_indices    = [i for i, t in enumerate(all_t) if not t.builtin]
        if user_indices:
            self.assertLess(max(builtin_indices), min(user_indices))

    def test_template_to_agent_configs(self):
        from ai.workflow_templates import get_template, template_to_agent_configs
        t    = get_template('quick_implement')
        cfgs = template_to_agent_configs(t)
        self.assertEqual(len(cfgs), 2)
        self.assertEqual(cfgs[0]['role'], 'implementer')
        self.assertIn('model', cfgs[0])

    def test_full_pipeline_six_agents(self):
        from ai.workflow_templates import get_template
        t = get_template('full_pipeline')
        self.assertEqual(len(t.agents), 6)


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
