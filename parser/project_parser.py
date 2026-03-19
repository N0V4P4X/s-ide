"""
parser/project_parser.py
=========================
Orchestrator: ties together the walker, per-language parsers, edge
resolver, layout engine, and doc auditor into a single parse pass.

Parse pipeline (with stage timings)
------------------------------------
1. init_project_config  — load/create side.project.json
2. walk_directory       — discover all source files
3. per-file parsing     — call the appropriate language parser
4. resolve_edges        — turn raw import strings into graph edges
5. assign_positions     — auto-layout for the node editor
6. audit_docs           — README / empty-module health check
7. write_graph_json     — auto-save .nodegraph.json to project root

Each stage is timed by a ParseTimer; results are stored in
graph.meta["perf"] for display in the GUI performance panel.

Auto-save
---------
After every successful parse, the full graph dict is written to:
    <project_root>/.nodegraph.json

This ensures the post-parse JSON always exists on disk without needing
the GUI to explicitly save it.
"""

from __future__ import annotations
import json
import os
from datetime import datetime, timezone

from parser.walker import walk_directory, make_node_id, FileInfo
from parser.parsers import PARSERS
import parser.parsers.python_parser   # ensure picklable
import concurrent.futures
from parser.resolve_edges import resolve_edges
from parser.layout import assign_positions
from parser.doc_check import audit_docs
from parser.project_config import init_project_config
from monitor.perf import ParseTimer

from graph.types import (
    FileNode, ImportRecord, ExportRecord, Definition,
    GraphMeta, ProjectGraph,
)


def _read_safe(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def _file_stats(path: str) -> tuple[int, str | None]:
    try:
        stat = os.stat(path)
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        return stat.st_size, mtime
    except Exception:
        return 0, None


def _make_node(file_info, parsed: dict, size: int, mtime: str | None, content: str | None) -> FileNode:
    lines = content.count("\n") + 1 if content else 0

    def to_import(r):
        return r if isinstance(r, ImportRecord) else ImportRecord(**r)
    def to_export(r):
        return r if isinstance(r, ExportRecord) else ExportRecord(**r)
    def to_def(r):
        return r if isinstance(r, Definition) else Definition(**r)

    return FileNode(
        id=make_node_id(file_info.relative_path),
        label=file_info.name,
        path=file_info.relative_path,
        full_path=file_info.full_path,
        category=file_info.category,
        ext=file_info.ext,
        lines=lines,
        size=size,
        modified=mtime,
        imports=[to_import(r) for r in (parsed.get("imports") or [])],
        exports=[to_export(r) for r in (parsed.get("exports") or [])],
        definitions=[to_def(r) for r in (parsed.get("definitions") or [])],
        tags=list(parsed.get("tags") or []),
        errors=list(parsed.get("errors") or []),
    )
def _parse_file_worker(file_info: FileInfo) -> FileNode:
    """Worker task for ProcessPoolExecutor."""
    content = _read_safe(file_info.full_path)
    size, mtime = _file_stats(file_info.full_path)
    # Re-import parsers inside worker if needed, but they are top-level so should work
    from parser.parsers import PARSERS
    
    parser_fn = PARSERS.get(file_info.ext)
    if parser_fn and content is not None:
        try:
            parsed = parser_fn(content, file_info.full_path)
        except Exception as exc:
            parsed = {"imports": [], "exports": [], "definitions": [],
                      "tags": [], "errors": [f"Parser crash: {exc}"]}
    else:
        parsed = {"imports": [], "exports": [], "definitions": [],
                  "tags": [], "errors": []}
        
    return _make_node(file_info, parsed, size, mtime, content)


def parse_project(root_dir: str, save_json: bool = True) -> ProjectGraph:
    """
    Full parse pipeline for a project directory.

    Parameters
    ----------
    root_dir  : absolute or relative path to the project
    save_json : if True (default), write .nodegraph.json to root_dir

    Returns a ProjectGraph ready for JSON serialisation.
    Stage timings are available at graph.to_dict()["meta"]["perf"].
    """
    timer    = ParseTimer()
    root_dir = os.path.abspath(root_dir)

    # 1. Config
    with timer.stage("config"):
        config = init_project_config(root_dir)
        extra_ignore = list(config.get("ignore") or [])
        versions_dir = (config.get("versions") or {}).get("dir", "versions")
        if versions_dir and versions_dir not in extra_ignore:
            extra_ignore.append(versions_dir)
        # Also ignore logs/ and build output dirs
        for _d in ("logs", "dist", "build", ".nodegraph.json"):
            if _d not in extra_ignore:
                extra_ignore.append(_d)

    # 2. Walk
    with timer.stage("walk"):
        files = walk_directory(root_dir, extra_ignore=extra_ignore)

    # Load existing graph for delta-updates
    old_nodes: dict[str, dict] = {}
    if os.path.isfile(os.path.join(root_dir, ".nodegraph.json")):
        try:
            with open(os.path.join(root_dir, ".nodegraph.json"), "r") as f:
                old_data = json.load(f)
                for n in old_data.get("nodes", []):
                    old_nodes[n["path"]] = n
        except Exception:
            pass

    # 3. Parse files (Delta + Parallel)
    nodes: list[FileNode] = []
    file_index: dict[str, str] = {}
    changed_files: list[FileInfo] = []

    with timer.stage("parse_files"):
        for file_info in files:
            size, mtime = _file_stats(file_info.full_path)
            old = old_nodes.get(file_info.relative_path)
            
            # Re-use node if mtime and size match
            if old and old.get("modified") == mtime and old.get("size") == size:
                # Reconstruct FileNode from dict
                node = FileNode(
                    id=old["id"], label=old["label"], path=old["path"],
                    full_path=old.get("full_path", file_info.full_path),
                    category=old.get("category", "other"),
                    ext=old.get("ext", ".py"), lines=old.get("lines", 0),
                    size=size, modified=mtime,
                    imports=[ImportRecord(**r) for r in old.get("imports", [])],
                    exports=[ExportRecord(**r) for r in old.get("exports", [])],
                    definitions=[Definition(**r) for r in old.get("definitions", [])],
                    tags=list(old.get("tags") or []),
                    errors=list(old.get("errors") or []),
                )
                nodes.append(node)
                file_index[node.path] = node.id
            else:
                changed_files.append(file_info)

        if changed_files:
            # Parse only changed files in parallel
            with concurrent.futures.ProcessPoolExecutor() as executor:
                results = list(executor.map(_parse_file_worker, changed_files))
            for node in results:
                nodes.append(node)
                file_index[node.path] = node.id

    # 4. Edges
    with timer.stage("resolve_edges"):
        edges = resolve_edges(nodes, file_index, root_dir)

    # 5. Layout
    with timer.stage("layout"):
        assign_positions(nodes, edges)

    # 6. Docs
    with timer.stage("doc_audit"):
        docs = audit_docs(root_dir, nodes)

    # 7. Language stats
    lang_stats: dict[str, dict] = {}
    for node in nodes:
        cat = node.category
        if cat not in lang_stats:
            lang_stats[cat] = {"files": 0, "lines": 0}
        lang_stats[cat]["files"] += 1
        lang_stats[cat]["lines"] += node.lines

    # Finalise perf — include write_json stage if it runs
    # We create a preliminary report now; write_json will add its stage after
    perf = timer.report()

    meta = GraphMeta(
        root=root_dir,
        parsed_at=datetime.now(tz=timezone.utc).isoformat(),
        parse_time_ms=perf["total_ms"],
        total_files=len(nodes),
        total_edges=len(edges),
        languages=lang_stats,
        docs=docs,
        project_name=config.get("name") or os.path.basename(root_dir),
        project_version=config.get("version") or "0.0.0",
        project_description=config.get("description") or "",
        project_run=dict(config.get("run") or {}),
        has_config=bool(config.get("_exists")),
        perf=perf,
    )

    graph = ProjectGraph(version="1.0.0", meta=meta, nodes=nodes, edges=edges)

    # 8. Auto-save .nodegraph.json (timed; updates meta.perf after the fact)
    if save_json:
        with timer.stage("write_json"):
            _write_graph_json(graph, root_dir)
        # Refresh perf with the write_json stage included
        final_perf = timer.report()
        graph.meta = GraphMeta(
            **{k: v for k, v in vars(meta).items() if k != "perf"},
            perf=final_perf,
        )
        # Overwrite the JSON with the complete timing
        _write_graph_json(graph, root_dir)

    return graph


def _write_graph_json(graph: ProjectGraph, root_dir: str) -> str:
    """
    Serialise the graph to <root_dir>/.nodegraph.json.
    perf data is already in graph.meta.perf via the GraphMeta dataclass.
    Returns the path written.
    """
    out_path = os.path.join(root_dir, ".nodegraph.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(graph.to_dict(), f, indent=2)
    return out_path
