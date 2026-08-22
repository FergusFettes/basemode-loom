"""Graph-shape metrics for a node's subtree.

Kept separate from `stats.py` (which scores node *utility* using expansion,
bookmark, and rating signals) because these metrics are pure topology: they
only need parent/child edges, nothing about what a node's content is worth.
As the stats surface grows, add new topology metrics as extra `SubtreeShape`
fields plus a computation step in `_shape_from_edges` — this module is meant
to stay the single place that answers "what does this subtree look like."
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from .store import GenerationStore


@dataclass(frozen=True)
class SubtreeShape:
    """Topology of the subtree rooted at `node_id`.

    `subtree_size` includes `node_id` itself; `descendant_count` doesn't.
    `avg_branching_factor` (descendant_count / internal_count) is 1.0 for a
    pure chain and grows with fan-out. `branchiness` — (leaf_count - 1) /
    (descendant_count - 1), 0.0 for descendant_count <= 1 — is 0.0 for a
    straight chain and 1.0 when every child of `node_id` is itself a leaf
    (maximally spread for its size), independent of how big the subtree is.
    """

    node_id: str
    subtree_size: int
    descendant_count: int
    child_count: int
    leaf_count: int
    internal_count: int
    max_depth: int
    max_width: int
    avg_branching_factor: float
    branchiness: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "subtree_size": self.subtree_size,
            "descendant_count": self.descendant_count,
            "child_count": self.child_count,
            "leaf_count": self.leaf_count,
            "internal_count": self.internal_count,
            "max_depth": self.max_depth,
            "max_width": self.max_width,
            "avg_branching_factor": self.avg_branching_factor,
            "branchiness": self.branchiness,
        }


def analyze_subtree(store: GenerationStore, node_id: str) -> SubtreeShape:
    """Compute topology metrics for the subtree rooted at `node_id`."""
    resolved = store.resolve_node_id(node_id)
    if resolved is None:
        raise KeyError(f"unknown node: {node_id}")

    edges = store.subtree_edges(resolved)
    return _shape_from_edges(resolved, edges)


def _shape_from_edges(
    node_id: str, edges: list[tuple[str, str | None]]
) -> SubtreeShape:
    """`edges` is a list of (id, parent_id) pairs for `node_id` and its descendants."""
    children_by_parent: dict[str, list[str]] = defaultdict(list)
    for child_id, parent_id in edges:
        if parent_id is not None:
            children_by_parent[parent_id].append(child_id)

    depth_by_id: dict[str, int] = {node_id: 0}
    stack = [node_id]
    while stack:
        current = stack.pop()
        for child_id in children_by_parent.get(current, []):
            depth_by_id[child_id] = depth_by_id[current] + 1
            stack.append(child_id)

    subtree_size = len(depth_by_id)
    descendant_count = subtree_size - 1
    child_count = len(children_by_parent.get(node_id, []))
    leaf_count = sum(1 for nid in depth_by_id if not children_by_parent.get(nid))
    internal_count = subtree_size - leaf_count
    max_depth = max(depth_by_id.values(), default=0)
    max_width = max(Counter(depth_by_id.values()).values(), default=0)

    avg_branching_factor = descendant_count / internal_count if internal_count else 0.0
    branchiness = (
        (leaf_count - 1) / (descendant_count - 1) if descendant_count > 1 else 0.0
    )

    return SubtreeShape(
        node_id=node_id,
        subtree_size=subtree_size,
        descendant_count=descendant_count,
        child_count=child_count,
        leaf_count=leaf_count,
        internal_count=internal_count,
        max_depth=max_depth,
        max_width=max_width,
        avg_branching_factor=avg_branching_factor,
        branchiness=branchiness,
    )
