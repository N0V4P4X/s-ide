"""
parser/resolve_edges.py
=======================
Converts raw import source strings (as written in code) into directed
graph edges between FileNodes.

Resolution strategy
-------------------
1. If the import source starts with '.' or '/', it's relative → try to
   find a matching node by probing extension variants and index files.
2. Otherwise it's external (stdlib, pip, npm) → create a virtual
   'ext_<pkg>' target node reference with is_external=True.
3. Relative paths that don't resolve to any known file are flagged as
   errors on the source node (broken import).

Python relative imports
-----------------------
'from . import foo'   → source='.' (same package)
'from ..utils import' → source='..utils'
Leading dots are converted to relative path components using the
importing file's directory depth.

Edge deduplication
------------------
(source_id, target_id, type) tuples are tracked; duplicate edges are
dropped. This handles cases like multiple named imports from the same
module being listed separately.
"""

from __future__ import annotations
import os
from graph.types import Edge, FileNode

# Edge type → visual/semantic label used by the renderer
EDGE_TYPE_MAP: dict[str, str] = {
    "es-default":       "import",
    "es-named":         "import",
    "es-namespace":     "import",
    "es-side-effect":   "import-side-effect",
    "cjs-require":      "require",
    "dynamic-import":   "import-dynamic",
    "re-export":        "reexport",
    "re-export-all":    "reexport",
    "source":           "shell-source",
    "script-call":      "shell-call",
    "from-import":      "import",
    "from-import-all":  "import",
    "import":           "import",
    "npm-dependency":   "npm-dep",
}

# Extension candidates tried in order when resolving bare paths
_EXT_CANDIDATES = ["", ".py", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".json", ".sh"]

# Index filenames tried when a path resolves to a directory
_INDEX_CANDIDATES = [
    "__init__.py", "index.js", "index.ts", "index.mjs", "index.jsx", "index.tsx"
]


def _resolve_python_relative(source: str, from_path: str) -> str:
    """
    Convert a Python relative import source to a relative filesystem path.

    'from . import foo'    → source='.'   → same dir
    'from .utils import'   → source='.utils'
    'from ..core import'   → source='..core'
    """
    # Count leading dots
    dots = 0
    while dots < len(source) and source[dots] == ".":
        dots += 1
    remainder = source[dots:].replace(".", "/")  # 'pkg.sub' → 'pkg/sub'

    # from_path is 'a/b/c.py'; its package dir is 'a/b'
    parts = from_path.replace("\\", "/").split("/")
    # Go up (dots - 1) levels from the file's directory
    base_parts = parts[:-1]  # strip filename
    for _ in range(dots - 1):
        if base_parts:
            base_parts.pop()

    if remainder:
        base_parts.append(remainder)

    return "/".join(base_parts) if base_parts else "."


def _is_relative(source: str) -> bool:
    """True if source looks like a relative path (starts with . or /)."""
    return source.startswith(".") or source.startswith("/")


def _is_python_relative(source: str) -> bool:
    """True for Python relative imports like '.', '..', '.utils', '..core'."""
    return bool(source) and source[0] == "." and (
        len(source) == 1 or source[1] in (".", "/", "") or
        (len(source) > 1 and source[1].isalpha())
    )


def _try_resolve(base_path: str, file_index: dict[str, str]) -> str | None:
    """
    Try base_path with extension candidates, then as directory with index files.
    Returns node_id if found, else None.
    """
    # Normalise: forward slashes, no leading ./
    norm = base_path.replace("\\", "/").lstrip("./")

    for ext in _EXT_CANDIDATES:
        candidate = norm + ext
        if candidate in file_index:
            return file_index[candidate]

    # Try as directory (package)
    for idx in _INDEX_CANDIDATES:
        candidate = f"{norm}/{idx}"
        if candidate in file_index:
            return file_index[candidate]

    return None


def resolve_edges(nodes: list[FileNode], file_index: dict[str, str], root_dir: str) -> list[Edge]:
    """
    Build the complete edge list from all nodes' imports (and re-exports).

    file_index: { relative_path → node_id }
    Returns a deduplicated list of Edge objects.
    """
    edges: list[Edge] = []
    edge_set: set[tuple] = set()   # (source_id, target_id, type) for deduplication
    edge_counter = 0

    for node in nodes:
        # Gather all import-like records (imports + re-export sources)
        all_imports = list(node.imports)
        for exp in node.exports:
            if exp.source:
                # Re-exports create an import edge too
                from graph.types import ImportRecord
                all_imports.append(ImportRecord(
                    type="re-export",
                    source=exp.source,
                    names=exp.names,
                    line=exp.line,
                ))

        for imp in all_imports:
            if not imp.source:
                continue

            source_str = imp.source
            resolved_id = None
            is_external = False
            external_pkg = None

            # ── Python relative imports (leading dots) ────────────────────
            if _is_python_relative(source_str) and node.ext in (".py", ".pyw"):
                rel_path = _resolve_python_relative(source_str, node.path)
                resolved_id = _try_resolve(rel_path, file_index)

            # ── Python bare names: 'from utils import X' or 'import utils' ──
            # Try local project tree first before marking external.
            # Order: file's own directory → project root → external.
            elif node.ext in (".py", ".pyw") and not _is_relative(source_str):
                as_path = source_str.replace(".", "/")
                from_dir = "/".join(node.path.replace("\\", "/").split("/")[:-1])
                if from_dir:
                    resolved_id = _try_resolve(f"{from_dir}/{as_path}", file_index)
                if resolved_id is None:
                    resolved_id = _try_resolve(as_path, file_index)
                if resolved_id is None:
                    is_external = True
                    external_pkg = source_str.split(".")[0]

            # ── JS/Shell relative paths ───────────────────────────────────────
            elif _is_relative(source_str):
                from_dir = "/".join(node.path.replace("\\", "/").split("/")[:-1])
                if from_dir:
                    joined = f"{from_dir}/{source_str.lstrip('./')}"
                else:
                    joined = source_str.lstrip("./")
                # Collapse ../ sequences
                parts = []
                for part in joined.replace("\\", "/").split("/"):
                    if part == "..":
                        if parts:
                            parts.pop()
                    elif part not in (".", ""):
                        parts.append(part)
                resolved_id = _try_resolve("/".join(parts), file_index)

            # ── External (stdlib / third-party) ──────────────────────────────
            else:
                is_external = True
                # For scoped packages like @org/pkg, keep the full name
                external_pkg = source_str.split("/")[0] if not source_str.startswith("@") \
                               else "/".join(source_str.split("/")[:2])

            edge_type = EDGE_TYPE_MAP.get(imp.type, "import")

            if resolved_id:
                key = (node.id, resolved_id, edge_type)
                if key not in edge_set:
                    edge_set.add(key)
                    edges.append(Edge(
                        id=f"e_{edge_counter}",
                        source=node.id,
                        target=resolved_id,
                        type=edge_type,
                        symbols=imp.names or ([imp.alias] if imp.alias else []),
                        line=imp.line,
                    ))
                    edge_counter += 1

            elif is_external and external_pkg:
                ext_id = "ext_" + "".join(c if c.isalnum() else "_" for c in external_pkg)
                key = (node.id, ext_id, "external")
                if key not in edge_set:
                    edge_set.add(key)
                    edges.append(Edge(
                        id=f"e_{edge_counter}",
                        source=node.id,
                        target=ext_id,
                        type="external",
                        symbols=imp.names or ([imp.alias] if imp.alias else []),
                        line=imp.line,
                        is_external=True,
                        external_pkg=external_pkg,
                    ))
                    edge_counter += 1

            else:
                # Unresolved relative import — flag as error on the node
                if not any(imp.source in e for e in node.errors):
                    node.errors.append(f"Unresolved import: '{imp.source}'")

    return edges


def collect_external_packages(edges: list[Edge]) -> list[dict]:
    """
    Aggregate all external package references across the graph.
    Useful for the 'external dependencies' panel in the visualizer.
    Returns list of {name, used_by, symbols} dicts sorted by usage count.
    """
    externals: dict[str, dict] = {}
    for edge in edges:
        if not edge.is_external or not edge.external_pkg:
            continue
        pkg = edge.external_pkg
        if pkg not in externals:
            externals[pkg] = {"name": pkg, "used_by": [], "symbols": set()}
        externals[pkg]["used_by"].append(edge.source)
        externals[pkg]["symbols"].update(edge.symbols)

    return sorted(
        [{"name": v["name"], "used_by": v["used_by"], "symbols": sorted(v["symbols"])}
         for v in externals.values()],
        key=lambda x: -len(x["used_by"])
    )
