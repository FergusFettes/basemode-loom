"""Shared tree summaries used by the picker and HTTP API."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .store import GenerationStore, Node

FACETS: tuple[str, ...] = ("category", "domain", "source", "model")
_NONE_LABEL = "(at root)"


def _flatten(text: str) -> str:
    return " ".join(text.split())


@dataclass
class TreeCatalogEntry:
    root: Node
    name: str | None
    node_count: int
    root_preview: str
    leaf_preview: str
    category: str = ""
    domain: str = ""
    sources: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    updated_at: str = ""
    archived: bool = False
    breadth: int = 1
    avg_branching_factor: float = 0.0
    branchiness: float = 0.0

    @property
    def source(self) -> str:
        return "/".join(self.sources)

    @property
    def players(self) -> str:
        return ", ".join(self.models)

    def facet_values(self, facet: str) -> tuple[str, ...]:
        if facet == "category":
            return (self.category,) if self.category else ()
        if facet == "domain":
            return (self.domain,) if self.domain else ()
        if facet == "source":
            return self.sources
        if facet == "model":
            return self.models
        return ()


def build_tree_catalog(store: GenerationStore) -> list[TreeCatalogEntry]:
    """Bulk-load picker-ready summaries without per-tree queries."""
    roots = store.roots()
    if not roots:
        return []
    counts = store.descendant_counts([root.id for root in roots])
    tree_meta = store.tree_index()
    facets = store.tree_facets()
    classifications = store.tree_classifications()

    leaf_ids = [
        current_id
        for root in roots
        if (current_id := tree_meta.get(root.tree_id, (None, None))[1])
        and current_id != root.id
    ]
    leaf_nodes = store.nodes_by_ids(leaf_ids)

    entries: list[TreeCatalogEntry] = []
    for root in roots:
        name, current_id = tree_meta.get(root.tree_id, (None, None))
        if current_id and current_id != root.id:
            current = leaf_nodes.get(current_id)
            leaf_preview = _flatten(current.text) if current else _NONE_LABEL
        else:
            leaf_preview = _NONE_LABEL

        facet = facets.get(root.tree_id, {})
        classification = classifications.get(root.tree_id, {})
        sources = tuple(facet.get("sources", []))
        if not sources and classification.get("source"):
            sources = (classification["source"],)

        entries.append(
            TreeCatalogEntry(
                root=root,
                name=name,
                node_count=counts.get(root.id, 0) + 1,
                root_preview=_flatten(root.text),
                leaf_preview=leaf_preview,
                category=classification.get("category", ""),
                domain=classification.get("domain", ""),
                sources=sources,
                models=tuple(model.split("/")[-1] for model in facet.get("models", [])),
            )
        )
    return entries


def query_tree_catalog(
    store: GenerationStore,
    *,
    archived: bool | None,
    category: list[str] | None,
    domain: list[str] | None,
    source: list[str] | None,
    model: list[str] | None,
    metadata_query: str,
    tree_ids: list[str] | None,
    sort: str,
    descending: bool,
    offset: int,
    limit: int,
) -> tuple[list[TreeCatalogEntry], int]:
    """Query one catalogue page without loading every tree into Python."""
    rows, total = store.tree_catalog_rows(
        archived=archived,
        category=category,
        domain=domain,
        source=source,
        model=model,
        metadata_query=metadata_query,
        tree_ids=tree_ids,
        sort=sort,
        descending=descending,
        offset=offset,
        limit=limit,
    )
    entries = []
    for row in rows:
        root = Node(
            id=row["root_id"],
            parent_id=None,
            text=row["root_text"],
            model=None,
            strategy=None,
            max_tokens=None,
            temperature=None,
            created_at=row["created_at"],
            metadata={},
            tree_id=row["tree_id"],
            kind="root",
        )
        sources = tuple(json.loads(row["sources_json"] or "[]"))
        models = tuple(
            value.split("/")[-1]
            for value in json.loads(row["models_json"] or "[]")
        )
        current_id = row["current_node_id"]
        leaf_preview = (
            _flatten(row["leaf_text"])
            if current_id and current_id != row["root_id"] and row["leaf_text"]
            else _NONE_LABEL
        )
        entries.append(
            TreeCatalogEntry(
                root=root,
                name=row["name"],
                node_count=int(row["node_count"]),
                root_preview=_flatten(row["root_text"]),
                leaf_preview=leaf_preview,
                category=row["category"] or "",
                domain=row["domain"] or "",
                sources=sources,
                models=models,
                updated_at=row["updated_at"],
                archived=bool(row["archived"]),
                breadth=int(row["breadth"]),
                avg_branching_factor=float(row["avg_branching_factor"]),
                branchiness=float(row["branchiness"]),
            )
        )
    return entries, total
