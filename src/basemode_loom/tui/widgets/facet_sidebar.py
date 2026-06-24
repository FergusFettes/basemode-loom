"""Left sidebar for the tree picker: search box, sort label, facet toggles.

Built from Textual primitives (``Input``, ``SelectionList``) so keyboard
handling, scrolling, and checkbox state come for free. The screen owns the
:class:`~basemode_loom.tui.widgets.picker_model.PickerModel`; this widget only
renders it and emits the built-in ``Input.Submitted`` /
``SelectionList.SelectedChanged`` messages, which the screen turns into model
mutations.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Label, SelectionList
from textual.widgets.selection_list import Selection

from .picker_model import FACETS, PickerModel

_FACET_LABELS = {
    "category": "Category",
    "domain": "Domain",
    "source": "Source",
    "model": "Model",
}


def facet_box_id(facet: str) -> str:
    return f"facet-box-{facet}"


def facet_list_id(facet: str) -> str:
    return f"facet-list-{facet}"


def facet_of_list_id(list_id: str | None) -> str | None:
    if list_id and list_id.startswith("facet-list-"):
        return list_id[len("facet-list-") :]
    return None


class FacetSidebar(VerticalScroll):
    """Search + sort + facet selection panel."""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="search…", id="picker-search")
        yield Label("", id="picker-sort")
        for facet in FACETS:
            with Vertical(id=facet_box_id(facet), classes="facet-box"):
                yield Label(_FACET_LABELS[facet], classes="facet-title")
                yield SelectionList[str](id=facet_list_id(facet))

    def populate(self, model: PickerModel, *, keyword: bool, message: str) -> None:
        """Fill the facet lists and configure the search box from the model.

        The box always does live substring filtering; when a keyword index is
        present, pressing Enter additionally ranks trees by relevance. So it is
        never disabled — only the placeholder reflects the available capability.
        """
        search = self.query_one("#picker-search", Input)
        search.placeholder = "search…" if keyword else "filter…"

        for facet in FACETS:
            box = self.query_one(f"#{facet_box_id(facet)}", Vertical)
            values = model.facet_values(facet)
            box.display = bool(values)
            if not values:
                continue
            selection_list = self.query_one(f"#{facet_list_id(facet)}", SelectionList)
            selected = model.active.get(facet, set())
            selection_list.clear_options()
            selection_list.add_options(
                Selection(f"{value}  ({count})", value, value in selected)
                for value, count in values
            )

    def set_sort_label(self, text: str) -> None:
        self.query_one("#picker-sort", Label).update(text)

    def focus_search(self) -> None:
        self.query_one("#picker-search", Input).focus()
