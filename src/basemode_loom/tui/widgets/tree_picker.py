from __future__ import annotations

from typing import TYPE_CHECKING

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.geometry import Region, Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

from .picker_model import _TreeEntry

if TYPE_CHECKING:
    from basemode_loom.store import GenerationStore

_CURSOR = "▸"
_ACTIVE = "●"  # marks the currently open tree
_LEAF_PREFIX = "  └ "
_NONE_LABEL = "(at root)"
_ENTRY_HEIGHT = 5


def _flatten(text: str) -> str:
    return " ".join(text.split())


def build_entries(store: GenerationStore) -> list[_TreeEntry]:
    """Bulk-load every tree into ``_TreeEntry`` rows in a single pass.

    Uses the store's bulk accessors (one query each) rather than per-root
    queries, which otherwise open thousands of SQLite connections on large
    corpora.
    """
    roots = store.roots()
    if not roots:
        return []
    counts = store.descendant_counts([r.id for r in roots])
    tree_meta = store.tree_index()
    facets = store.tree_facets()
    classifications = store.tree_classifications()

    leaf_ids = [
        last_id
        for root in roots
        if (last_id := tree_meta.get(root.tree_id, (None, None))[1])
        and last_id != root.id
    ]
    leaf_nodes = store.nodes_by_ids(leaf_ids) if leaf_ids else {}

    entries: list[_TreeEntry] = []
    for root in roots:
        node_count = counts.get(root.id, 0) + 1  # +1 for the root itself
        name, last_id = tree_meta.get(root.tree_id, (None, None))
        if last_id and last_id != root.id:
            node = leaf_nodes.get(last_id)
            leaf_preview = _flatten(node.text) if node else _NONE_LABEL
        else:
            leaf_preview = _NONE_LABEL

        facet = facets.get(root.tree_id, {})
        classification = classifications.get(root.tree_id, {})
        # Prefer node-derived sources; fall back to the tree-level source.
        sources = tuple(facet.get("sources", []))
        if not sources and classification.get("source"):
            sources = (classification["source"],)
        models = tuple(m.split("/")[-1] for m in facet.get("models", []))

        entries.append(
            _TreeEntry(
                root=root,
                name=name,
                node_count=node_count,
                root_preview=_flatten(root.text),
                leaf_preview=leaf_preview,
                category=classification.get("category", ""),
                domain=classification.get("domain", ""),
                sources=sources,
                models=models,
            )
        )
    return entries


class TreePickerView(ScrollView):
    """Scrollable list of trees. Pure view: the screen feeds it visible entries."""

    DEFAULT_CSS = """
    TreePickerView {
        height: 1fr;
        overflow-y: auto;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._entries: list[_TreeEntry] = []
        self._cursor: int = 0
        self._current_root_id: str = ""
        self._relevance: bool = False

    def set_current_root_id(self, root_id: str) -> None:
        self._current_root_id = root_id

    def set_entries(
        self,
        entries: list[_TreeEntry],
        *,
        relevance: bool = False,
        focus_root_id: str | None = None,
    ) -> None:
        """Replace the visible entries, keeping the cursor on the same tree."""
        prev_id = focus_root_id
        if prev_id is None and 0 <= self._cursor < len(self._entries):
            prev_id = self._entries[self._cursor].root.id

        self._entries = entries
        self._relevance = relevance
        self._cursor = 0
        if prev_id:
            for i, entry in enumerate(entries):
                if entry.root.id == prev_id:
                    self._cursor = i
                    break
        self._update_virtual_size()
        self.refresh(layout=True)
        self._scroll_cursor_visible()

    def move(self, delta: int) -> None:
        self._cursor = max(0, min(self._cursor + delta, len(self._entries) - 1))
        self.refresh()
        self._scroll_cursor_visible()

    def selected_root_id(self) -> str | None:
        if self._entries:
            return self._entries[self._cursor].root.id
        return None

    def root_ids(self) -> list[str]:
        return [entry.root.id for entry in self._entries]

    def visible_count(self) -> int:
        return len(self._entries)

    def get_content_height(self, container, viewport, width: int) -> int:
        return max(1, len(self._entries) * _ENTRY_HEIGHT)

    def render(self) -> Text:
        if not self._entries:
            return Text(self._empty_message(), style=Style(dim=True))
        width = self.size.width or 80
        result = Text(no_wrap=True, overflow="fold")
        for i, entry in enumerate(self._entries):
            selected = i == self._cursor
            is_open = entry.root.id == self._current_root_id
            hdr_style, body_style = self._styles(selected, is_open)
            result.append(
                self._header_line(entry, selected, is_open, width) + "\n", style=hdr_style
            )
            result.append(self._meta_line(entry, width) + "\n", style=Style(dim=True))
            indent = "   "
            result.append(
                indent + entry.root_preview[: max(0, width - len(indent) - 1)] + "\n",
                style=body_style,
            )
            result.append(
                (_LEAF_PREFIX + entry.leaf_preview)[:width] + "\n", style=body_style
            )
            result.append("\n")
        return result

    def render_line(self, y: int) -> Strip:
        width = self.scrollable_content_region.width or self.size.width or 80
        if not self._entries:
            if y == 0:
                return self._strip(self._empty_message(), Style(dim=True), width)
            return Strip.blank(width, Style())

        line_number = self.scroll_offset.y + y
        if line_number < 0 or line_number >= len(self._entries) * _ENTRY_HEIGHT:
            return Strip.blank(width, Style())

        entry_index, entry_line = divmod(line_number, _ENTRY_HEIGHT)
        entry = self._entries[entry_index]
        selected = entry_index == self._cursor
        is_open = entry.root.id == self._current_root_id
        hdr_style, body_style = self._styles(selected, is_open)

        if entry_line == 0:
            return self._strip(
                self._header_line(entry, selected, is_open, width), hdr_style, width
            )
        if entry_line == 1:
            return self._strip(self._meta_line(entry, width), Style(dim=True), width)
        if entry_line == 2:
            indent = "   "
            return self._strip(
                indent + entry.root_preview[: max(0, width - len(indent) - 1)],
                body_style,
                width,
            )
        if entry_line == 3:
            return self._strip(
                (_LEAF_PREFIX + entry.leaf_preview)[:width], body_style, width
            )
        return Strip.blank(width, Style())

    def on_resize(self, event: events.Resize) -> None:
        self._update_virtual_size()
        self.refresh()
        self._scroll_cursor_visible()

    # --- rendering helpers ---

    @staticmethod
    def _styles(selected: bool, is_open: bool) -> tuple[Style, Style]:
        if selected:
            return Style(bold=True), Style(bold=True)
        if is_open:
            return Style(italic=True), Style(dim=True)
        return Style(), Style(dim=True)

    def _header_line(
        self, entry: _TreeEntry, selected: bool, is_open: bool, width: int
    ) -> str:
        name = entry.name or entry.root.id[:8]
        count_str = f"{entry.node_count} node{'s' if entry.node_count != 1 else ''}"
        delete_str = "d delete" if selected else "delete"
        meta_str = f"{count_str}  [{delete_str}]"
        cursor_char = _CURSOR if selected else " "
        open_char = _ACTIVE if is_open else " "
        header_left = f"{cursor_char}{open_char} {name}"
        gap = max(2, width - len(header_left) - len(meta_str))
        return (header_left + " " * gap + meta_str)[:width]

    def _meta_line(self, entry: _TreeEntry, width: int) -> str:
        parts = [entry.root.id[:8], self._format_date(entry.root.created_at)]
        parts.extend(p for p in (entry.category, entry.domain) if p)
        if entry.source:
            parts.append(entry.source)
        if entry.players:
            parts.append(entry.players)
        return ("   " + "  ·  ".join(p for p in parts if p))[:width]

    @staticmethod
    def _format_date(created_at: str) -> str:
        return created_at[:16].replace("T", " ") if created_at else ""

    def _empty_message(self) -> str:
        return "No trees match the current filters."

    def _update_virtual_size(self) -> None:
        self.virtual_size = Size(
            max(1, self.size.width or 80),
            max(1, len(self._entries) * _ENTRY_HEIGHT),
        )

    def _scroll_cursor_visible(self) -> None:
        if not self._entries:
            return
        y = self._cursor * _ENTRY_HEIGHT
        self.scroll_to_region(
            Region(0, y, max(1, self.size.width), _ENTRY_HEIGHT),
            animate=False,
            immediate=True,
            x_axis=False,
        )

    @staticmethod
    def _strip(text: str, style: Style, width: int) -> Strip:
        strip = Strip([Segment(text[:width], style)])
        return strip.adjust_cell_length(width, style)
