"""Pure filter / sort / search state for the tree picker.

No Textual imports live here: this module is the testable heart of the picker.
The screen owns one :class:`PickerModel`, mutates it in response to the sidebar
(facet toggles, search box) and keys, and renders ``model.visible()``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from basemode_loom.store import Node

# Facets exposed in the sidebar, in display order. Each maps an entry to the set
# of values it has for that facet (single-valued category/domain; multi-valued
# source/model). Matching is OR within a facet, AND across facets.
FACETS: tuple[str, ...] = ("category", "domain", "source", "model")

# Sort modes cycled by `s`. "relevance" is only meaningful (and auto-selected)
# while a search query is active.
_SORT_MODES = ["recent", "oldest", "nodes", "name"]
_SORT_KEYS: dict[str, Callable[[_TreeEntry], object]] = {
    "recent": lambda e: e.root.created_at,
    "oldest": lambda e: e.root.created_at,
    "nodes": lambda e: (e.node_count, e.root.created_at),
    "name": lambda e: (e.name or e.root.id).lower(),
}
_SORT_REVERSE = {"recent": True, "oldest": False, "nodes": True, "name": False}


@dataclass
class _TreeEntry:
    root: Node
    name: str | None
    node_count: int
    root_preview: str  # flattened first-paragraph text
    leaf_preview: str  # text of the checked-out leaf node
    category: str = ""
    domain: str = ""
    sources: tuple[str, ...] = ()  # import source(s), node- or tree-derived
    models: tuple[str, ...] = ()  # distinct model short-names in the tree

    @property
    def source(self) -> str:
        """Display string for the meta line."""
        return "/".join(self.sources)

    @property
    def players(self) -> str:
        """Display string for the meta line: distinct model short-names."""
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


@dataclass
class PickerModel:
    """Holds the full entry set plus the active facet / text / search state."""

    all_entries: list[_TreeEntry] = field(default_factory=list)
    sort_mode: str = _SORT_MODES[0]
    text_filter: str = ""
    active: dict[str, set[str]] = field(default_factory=dict)
    query: str = ""
    ranking: dict[str, float] | None = None  # tree_id -> relevance score

    # --- mutation ---

    def set_entries(self, entries: list[_TreeEntry]) -> None:
        self.all_entries = entries

    def toggle_facet(self, facet: str, value: str) -> None:
        selected = self.active.setdefault(facet, set())
        if value in selected:
            selected.discard(value)
        else:
            selected.add(value)
        if not selected:
            self.active.pop(facet, None)

    def set_facet(self, facet: str, values: set[str]) -> None:
        """Replace the selected values for a facet (used by SelectionList sync)."""
        if values:
            self.active[facet] = set(values)
        else:
            self.active.pop(facet, None)

    def set_text_filter(self, text: str) -> None:
        self.text_filter = text or ""

    def set_query(self, query: str, ranking: dict[str, float] | None) -> None:
        self.query = query or ""
        self.ranking = ranking if self.query else None
        if self.query and ranking is not None:
            self.sort_mode = "relevance"
        elif self.sort_mode == "relevance":
            self.sort_mode = _SORT_MODES[0]

    def clear_query(self) -> None:
        self.set_query("", None)

    def cycle_sort(self) -> str:
        # "relevance" is transient and not part of the manual cycle.
        idx = _SORT_MODES.index(self.sort_mode) if self.sort_mode in _SORT_MODES else -1
        self.sort_mode = _SORT_MODES[(idx + 1) % len(_SORT_MODES)]
        return self.sort_mode

    def clear_all(self) -> None:
        self.active.clear()
        self.text_filter = ""
        self.clear_query()

    # --- derived views ---

    def visible(self) -> list[_TreeEntry]:
        flt = self.text_filter.strip().lower()
        ranking = self.ranking
        result = [
            entry
            for entry in self.all_entries
            if self._matches_facets(entry)
            and self._matches_text(entry, flt)
            and (ranking is None or entry.root.tree_id in ranking)
        ]
        if ranking is not None and self.sort_mode == "relevance":
            result.sort(key=lambda e: ranking.get(e.root.tree_id, 0.0), reverse=True)
        else:
            mode = self.sort_mode if self.sort_mode in _SORT_KEYS else _SORT_MODES[0]
            result.sort(key=_SORT_KEYS[mode], reverse=_SORT_REVERSE[mode])
        return result

    def facet_values(self, facet: str) -> list[tuple[str, int]]:
        """Distinct values + counts for a facet, most common first."""
        counter: Counter[str] = Counter()
        for entry in self.all_entries:
            for value in entry.facet_values(facet):
                counter[value] += 1
        return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))

    def has_facet_values(self, facet: str) -> bool:
        return any(entry.facet_values(facet) for entry in self.all_entries)

    @property
    def total_count(self) -> int:
        return len(self.all_entries)

    @property
    def filters_active(self) -> bool:
        return bool(self.active or self.text_filter or self.query)

    # --- helpers ---

    def _matches_facets(self, entry: _TreeEntry) -> bool:
        for facet, selected in self.active.items():
            if not selected:
                continue
            if not selected.intersection(entry.facet_values(facet)):
                return False
        return True

    @staticmethod
    def _matches_text(entry: _TreeEntry, flt: str) -> bool:
        if not flt:
            return True
        haystack = " ".join(
            (
                entry.name or "",
                entry.category,
                entry.domain,
                entry.source,
                entry.players,
                entry.root.id,
            )
        ).lower()
        return flt in haystack
