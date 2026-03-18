"""
parser/layout.py
================
Assigns 2D canvas positions to FileNodes for the node editor's
initial auto-layout. The user can re-arrange nodes freely after load;
these are just starting positions.

Algorithm: Layered Topological Layout
--------------------------------------
1. Build an adjacency structure from edges.
2. Assign each node a 'depth' (layer) via a modified BFS/topo-sort.
   Nodes with no incoming edges start at depth 0.
3. Within each layer, spread nodes vertically with consistent gaps.
4. Nodes in cycles or unreachable from layer-0 are placed in an
   'orphan' row above the main graph.

Constants NODE_W, NODE_H, LAYER_GAP, NODE_GAP control spacing and
can be tuned to match the renderer's node card dimensions.
"""

from __future__ import annotations
from collections import deque
from graph.types import FileNode, Edge, Position

# ── Layout constants ──────────────────────────────────────────────────────────
NODE_W    = 240   # node card width  (pixels / canvas units)
NODE_H    = 160   # node card height
LAYER_GAP = 340   # horizontal gap between layers
NODE_GAP  = 210   # vertical gap between nodes in the same layer


def assign_positions(nodes: list[FileNode], edges: list[Edge]) -> None:
    """
    Mutates each FileNode's .position field in-place.
    Only considers internal edges (ignores external package edges).
    """
    if not nodes:
        return

    node_ids = {n.id for n in nodes}
    node_map = {n.id: n for n in nodes}

    # Count incoming internal edges per node
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
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        current_depth = depths[node_id]
        for target in adjacency.get(node_id, []):
            new_depth = current_depth + 1
            if new_depth > depths.get(target, 0):
                depths[target] = new_depth
            queue.append(target)

    # Group nodes by depth (layer)
    layers: dict[int, list[str]] = {}
    for node_id, depth in depths.items():
        if node_id not in visited:
            continue   # handle below as orphan
        layers.setdefault(depth, []).append(node_id)

    # Assign positions layer by layer
    for depth, layer_ids in sorted(layers.items()):
        # Sort within layer: config files to bottom, entrypoints to top
        def _sort_key(nid: str) -> int:
            node = node_map.get(nid)
            if not node:
                return 0
            if "entrypoint" in node.tags:
                return -100
            if node.category == "config":
                return 100
            if node.category == "docs":
                return 90
            return 0

        layer_ids.sort(key=_sort_key)
        total_height = len(layer_ids) * NODE_GAP
        for i, node_id in enumerate(layer_ids):
            node = node_map.get(node_id)
            if node:
                node.position = Position(
                    x=float(depth * LAYER_GAP),
                    y=float(i * NODE_GAP - total_height / 2),
                )

    # Place unvisited nodes (cycle members, isolated) in orphan row
    orphan_x = 0.0
    for node in nodes:
        if node.position is None:
            node.position = Position(x=orphan_x, y=float(-(NODE_H * 4)))
            orphan_x += NODE_W + 40
