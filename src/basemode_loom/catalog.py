"""Shared tree summaries used by the picker and HTTP API."""

from __future__ import annotations

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
