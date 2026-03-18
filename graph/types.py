"""
graph/types.py
==============
Dataclass definitions for the S-IDE project graph.

These types are the contract between the parser and every consumer
(visualizer, process manager, version manager, bundler, etc.).
Keeping them here prevents circular imports and makes the data model
easy to read in one place.

Graph structure overview
------------------------
ProjectGraph
  └── meta: GraphMeta          -- project info, timing, language stats, doc audit
  └── nodes: list[FileNode]    -- one node per source file
  └── edges: list[Edge]        -- directed dependency between two nodes

FileNode contains:
  - identity:    id, label, path, category, ext
  - metrics:     lines, size, modified
  - semantics:   imports, exports, definitions, tags, errors
  - layout:      position (x, y) assigned by layout engine
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional


# ── Semantic sub-structures ───────────────────────────────────────────────────

@dataclass
class ImportRecord:
    """A single import statement extracted from a source file."""
    type: str           # 'import', 'from-import', 'from-import-all', 'es-default', etc.
    source: str         # the imported module/path string as written in source
    line: Optional[int] = None
    names: list[str] = field(default_factory=list)   # named symbols imported
    alias: Optional[str] = None                       # 'import X as alias'


@dataclass
class ExportRecord:
    """A single exported symbol or re-export."""
    type: str           # 'implicit', '__all__', 'named', 'default', 'declaration', etc.
    name: Optional[str] = None
    names: list[str] = field(default_factory=list)
    source: Optional[str] = None   # for re-exports: the module re-exported from
    kind: Optional[str] = None     # 'function', 'class', 'variable', etc.
    line: Optional[int] = None


@dataclass
class Definition:
    """A named symbol defined in a file (function, class, variable, etc.)."""
    name: str
    kind: str           # 'function', 'class', 'method', 'arrow-function', 'component', etc.
    line: Optional[int] = None
    end_line: Optional[int] = None   # last line of body
    indent: int = 0
    is_async: bool = False
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)   # for classes: base class names
    # Data-flow fields (Python: full; others: partial)
    args: list = field(default_factory=list)       # [(name, type_hint_str), ...]
    return_type: str = ""
    calls: list[str] = field(default_factory=list) # functions/methods called in body
    raises: list[str] = field(default_factory=list)
    complexity: int = 0                            # cyclomatic complexity estimate


@dataclass
class Position:
    """2D canvas position for the node editor."""
    x: float
    y: float


# ── Core graph nodes ──────────────────────────────────────────────────────────

@dataclass
class FileNode:
    """
    Represents one source file in the project graph.

    id          -- stable identifier derived from relative path
    label       -- display name (filename)
    path        -- relative path from project root
    full_path   -- absolute path on disk
    category    -- visual grouping: 'python', 'javascript', 'config', etc.
    ext         -- file extension (with dot)
    lines       -- total line count
    size        -- file size in bytes
    modified    -- ISO-8601 mtime string

    imports     -- all import/require/source statements found
    exports     -- all exported symbols found
    definitions -- all function/class/variable definitions found
    tags        -- framework/role tags: 'flask', 'entrypoint', 'react', etc.
    errors      -- parse errors or unresolved-import warnings

    position    -- x/y assigned by layout engine, None until layout runs
    """
    id: str
    label: str
    path: str
    full_path: str
    category: str
    ext: str
    lines: int = 0
    size: int = 0
    modified: Optional[str] = None

    imports: list[ImportRecord] = field(default_factory=list)
    exports: list[ExportRecord] = field(default_factory=list)
    definitions: list[Definition] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    position: Optional[Position] = None

    def to_dict(self) -> dict:
        """Serialise to plain dict (JSON-safe)."""
        return {
            "id": self.id,
            "label": self.label,
            "path": self.path,
            "fullPath": self.full_path,
            "category": self.category,
            "ext": self.ext,
            "lines": self.lines,
            "size": self.size,
            "modified": self.modified,
            "imports": [vars(i) for i in self.imports],
            "exports": [vars(e) for e in self.exports],
            "definitions": [vars(d) for d in self.definitions],
            "tags": self.tags,
            "errors": self.errors,
            "position": vars(self.position) if self.position else None,
        }


# ── Edges ─────────────────────────────────────────────────────────────────────

@dataclass
class Edge:
    """
    A directed dependency edge between two FileNodes (or a node and an
    external package virtual node).

    source       -- FileNode.id of the importing file
    target       -- FileNode.id of the imported file (or 'ext_<pkg>' for externals)
    type         -- visual/semantic type: 'import', 'require', 'reexport',
                    'external', 'shell-source', 'npm-dep', etc.
    symbols      -- symbol names flowing across this edge
    line         -- source line where the import appears
    is_external  -- True for npm/pip/stdlib deps not present in the project tree
    external_pkg -- package name for external edges
    """
    id: str
    source: str
    target: str
    type: str
    symbols: list[str] = field(default_factory=list)
    line: Optional[int] = None
    is_external: bool = False
    external_pkg: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "symbols": self.symbols,
            "line": self.line,
            "isExternal": self.is_external,
            "externalPackage": self.external_pkg,
        }


# ── Doc audit ─────────────────────────────────────────────────────────────────

@dataclass
class DocWarning:
    type: str           # 'missing-readme', 'stale-readme', 'empty-module'
    severity: str       # 'warning', 'info'
    message: str
    dir: Optional[str] = None
    node_id: Optional[str] = None
    readme_path: Optional[str] = None
    affected_files: list[str] = field(default_factory=list)
    stale_since: Optional[str] = None


@dataclass
class DocAudit:
    healthy: bool
    missing_readmes: int
    stale_readmes: int
    empty_modules: int
    total_warnings: int
    warnings: list[DocWarning] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "summary": {
                "missingReadmes": self.missing_readmes,
                "staleReadmes": self.stale_readmes,
                "emptyModules": self.empty_modules,
                "total": self.total_warnings,
            },
            "warnings": [vars(w) for w in self.warnings],
        }


# ── Top-level graph ───────────────────────────────────────────────────────────

@dataclass
class GraphMeta:
    root: str
    parsed_at: str
    parse_time_ms: int
    total_files: int
    total_edges: int
    languages: dict[str, dict]   # category -> {files, lines}
    docs: DocAudit
    project_name: str
    project_version: str
    project_description: str
    project_run: dict[str, str]
    has_config: bool
    perf: dict = field(default_factory=dict)  # per-stage timing from ParseTimer


@dataclass
class ProjectGraph:
    """
    The complete parsed representation of a project.
    Serialised to .nodegraph.json and consumed by the visualizer.
    """
    version: str        # schema version, currently '1.0.0'
    meta: GraphMeta
    nodes: list[FileNode]
    edges: list[Edge]

    def to_dict(self) -> dict:
        lang = self.meta.languages
        return {
            "version": self.version,
            "meta": {
                "root": self.meta.root,
                "parsedAt": self.meta.parsed_at,
                "parseTime": self.meta.parse_time_ms,
                "perf":       self.meta.perf,
                "totalFiles": self.meta.total_files,
                "totalEdges": self.meta.total_edges,
                "languages": lang,
                "docs": self.meta.docs.to_dict(),
                "project": {
                    "name":        self.meta.project_name,
                    "version":     self.meta.project_version,
                    "description": self.meta.project_description,
                    "run":         self.meta.project_run,
                    "hasConfig":   self.meta.has_config,
                },
            },
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }
