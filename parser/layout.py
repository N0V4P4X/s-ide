# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
parser/layout.py
================
Assigns 2D canvas positions to FileNodes for the node graph's initial
auto-layout. Positions are starting points only — the user can rearrange
freely.

Algorithm: Clustered Directory Layout
---------------------------------------
1. Group nodes by their top-level directory (ai/, gui/, parser/, etc.).
2. Lay clusters out in a grid, sized by cluster population.
3. Within each cluster, run a layered topo-sort on the intra-cluster
   dependency edges, falling back to a simple grid for isolated nodes.
4. Place orphans (no edges, no clear cluster) in a tidy row at the bottom.

This produces a human-readable map where related modules live near each
other and import arrows flow left-to-right within a cluster, with
inter-cluster arrows visible as long crossing lines.

Constants
---------
NODE_W, NODE_H      : rendered card dimensions
CLUSTER_PAD         : internal padding inside a cluster bounding box
CLUSTER_GAP         : gap between cluster bounding boxes
LAYER_GAP           : horizontal gap between dependency layers inside a cluster
NODE_GAP            : vertical gap between nodes within a layer
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from graph.types import FileNode, Edge, Position


# Layout constants
NODE_W      = 240
NODE_H      = 160
CLUSTER_PAD = 80
CLUSTER_GAP = 160
LAYER_GAP   = 340
NODE_GAP    = 220


def assign_positions(nodes: list[FileNode], edges: list[Edge]) -> None:
    """Mutate each FileNode's .position field in-place."""
    if not nodes:
        return

    node_ids = {n.id for n in nodes}
    int_edges = [
        e for e in edges
        if not e.is_external
        and e.source in node_ids
        and e.target in node_ids
    ]

    # Group into clusters by top-level directory
    clusters: dict[str, list[FileNode]] = defaultdict(list)
    for node in nodes:
        clusters[_cluster_key(node)].append(node)

    sorted_clusters = sorted(
        clusters.items(),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )

    cluster_bounds = []
    cluster_layouts = []

    for _key, cnodes in sorted_clusters:
        cids = {n.id for n in cnodes}
        cedges = [e for e in int_edges if e.source in cids and e.target in cids]
        layout = _layout_cluster(cnodes, cedges)
        cluster_layouts.append(layout)

        if layout:
            xs = [p[0] for p in layout.values()]
            ys = [p[1] for p in layout.values()]
            w = max(xs) - min(xs) + NODE_W + CLUSTER_PAD * 2
            h = max(ys) - min(ys) + NODE_H + CLUSTER_PAD * 2
        else:
            w = NODE_W + CLUSTER_PAD * 2
            h = NODE_H + CLUSTER_PAD * 2
        cluster_bounds.append((0.0, 0.0, w, h))

    origins = _grid_arrange(cluster_bounds)

    for (_key, cnodes), layout, (ox, oy) in zip(
            sorted_clusters, cluster_layouts, origins):
        nmap = {n.id: n for n in cnodes}
        if layout:
            min_x = min(p[0] for p in layout.values())
            min_y = min(p[1] for p in layout.values())
        else:
            min_x = min_y = 0.0

        for nid, (lx, ly) in layout.items():
            node = nmap.get(nid)
            if node:
                node.position = Position(
                    x=float(ox + CLUSTER_PAD + (lx - min_x)),
                    y=float(oy + CLUSTER_PAD + (ly - min_y)),
                )

        orphan_x = ox + CLUSTER_PAD
        for node in cnodes:
            if node.position is None:
                node.position = Position(x=float(orphan_x),
                                          y=float(oy + CLUSTER_PAD))
                orphan_x += NODE_W + 40



def assign_positions_flat(nodes: list[FileNode], edges: list[Edge]) -> None:
    """
    Original vertical topo-sort layout — all nodes in one global graph.

    Columns = import depth layers (left to right).
    Rows    = nodes within each column, sorted by category, spread vertically.
    Entrypoints float to the top, config/docs sink to the bottom.

    This is the compact "waterfall" view: visually dense, good for seeing
    the full call-graph shape at a glance. Not clustered by directory.
    """
    if not nodes:
        return

    node_ids = {n.id for n in nodes}
    node_map = {n.id: n for n in nodes}

    in_degree: dict[str, int] = {n.id: 0 for n in nodes}
    adjacency: dict[str, list[str]] = {n.id: [] for n in nodes}

    for edge in edges:
        if edge.is_external:
            continue
        if edge.source in node_ids and edge.target in node_ids:
            in_degree[edge.target] = in_degree.get(edge.target, 0) + 1
            adjacency[edge.source].append(edge.target)

    # BFS topo-sort to assign depths
    depths: dict[str, int] = {n.id: 0 for n in nodes}
    queue: deque[str] = deque(
        n.id for n in nodes if in_degree.get(n.id, 0) == 0
    )
    visited: set[str] = set()

    while queue:
        nid = queue.popleft()
        if nid in visited:
            continue
        visited.add(nid)
        for target in adjacency.get(nid, []):
            nd = depths[nid] + 1
            if nd > depths.get(target, 0):
                depths[target] = nd
            queue.append(target)

    # Group by depth
    layers: dict[int, list[str]] = {}
    for nid, d in depths.items():
        if nid in visited:
            layers.setdefault(d, []).append(nid)

    FLAT_LAYER_GAP = 340
    FLAT_NODE_GAP  = 210

    for depth, layer_ids in sorted(layers.items()):
        layer_ids = sorted(layer_ids, key=lambda n: _sort_key(node_map.get(n)))
        total_h = (len(layer_ids) - 1) * FLAT_NODE_GAP
        for i, nid in enumerate(layer_ids):
            node = node_map.get(nid)
            if node:
                node.position = Position(
                    x=float(depth * FLAT_LAYER_GAP),
                    y=float(i * FLAT_NODE_GAP - total_h / 2),
                )

    # Orphans (cycle members, isolated) in a row above the main graph
    orphan_x = 0.0
    for node in nodes:
        if node.position is None:
            node.position = Position(x=orphan_x, y=float(-(NODE_H * 4)))
            orphan_x += NODE_W + 40

def _cluster_key(node: FileNode) -> str:
    path = (getattr(node, "path", "") or getattr(node, "id", ""))
    path = path.replace("\\", "/").lstrip("./")
    parts = path.split("/")
    return parts[0] if len(parts) > 1 else "root"


def _layout_cluster(
    nodes: list[FileNode], edges: list[Edge]
) -> dict[str, tuple[float, float]]:
    if not nodes:
        return {}
    if len(nodes) == 1:
        return {nodes[0].id: (0.0, 0.0)}

    node_ids = {n.id for n in nodes}
    nmap     = {n.id: n for n in nodes}
    in_deg   = {n.id: 0 for n in nodes}
    adj      = {n.id: [] for n in nodes}

    for e in edges:
        if e.source in node_ids and e.target in node_ids:
            in_deg[e.target] = in_deg.get(e.target, 0) + 1
            adj[e.source].append(e.target)

    depths  = {n.id: 0 for n in nodes}
    queue   = deque(n.id for n in nodes if in_deg.get(n.id, 0) == 0)
    visited: set[str] = set()

    while queue:
        nid = queue.popleft()
        if nid in visited:
            continue
        visited.add(nid)
        for target in adj.get(nid, []):
            nd = depths[nid] + 1
            if nd > depths.get(target, 0):
                depths[target] = nd
            queue.append(target)

    layers: dict[int, list[str]] = {}
    for nid, d in depths.items():
        if nid in visited:
            layers.setdefault(d, []).append(nid)

    positions: dict[str, tuple[float, float]] = {}
    for depth, layer_ids in sorted(layers.items()):
        layer_ids = sorted(layer_ids, key=lambda n: _sort_key(nmap.get(n)))
        total_h = (len(layer_ids) - 1) * NODE_GAP
        for i, nid in enumerate(layer_ids):
            positions[nid] = (
                float(depth * LAYER_GAP),
                float(i * NODE_GAP - total_h / 2),
            )

    ox, oy = 0.0, -float(NODE_H * 3)
    for node in nodes:
        if node.id not in positions:
            positions[node.id] = (ox, oy)
            ox += NODE_W + 40

    return positions


def _sort_key(node: "FileNode | None") -> int:
    if not node:
        return 0
    tags = getattr(node, "tags", []) or []
    cat  = getattr(node, "category", "") or ""
    if "entrypoint" in tags:
        return -100
    if cat == "config":
        return 100
    if cat == "docs":
        return 90
    return 0


def _grid_arrange(
    bounds: list[tuple[float, float, float, float]]
) -> list[tuple[float, float]]:
    if not bounds:
        return []
    n    = len(bounds)
    rows = max(1, round(math.sqrt(n / 1.6)))
    cols = max(1, math.ceil(n / rows))

    origins = []
    cx = cy = row_h = 0.0
    col = 0
    for _x, _y, w, h in bounds:
        origins.append((cx, cy))
        row_h = max(row_h, h)
        cx += w + CLUSTER_GAP
        col += 1
        if col >= cols:
            col = 0
            cx  = 0.0
            cy += row_h + CLUSTER_GAP
            row_h = 0.0
    return origins

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
