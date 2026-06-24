from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, SelectionList, Static

from ...retrieval import get_backend
from ...store import GenerationStore
from ..widgets.facet_sidebar import FacetSidebar, facet_of_list_id
from ..widgets.picker_model import PickerModel
from ..widgets.tree_picker import TreePickerView, build_entries


class TreePickerScreen(ModalScreen[str | None]):
    """Full-screen tree browser. Returns selected root_id or None on cancel."""

    DEFAULT_CSS = """
    TreePickerScreen #picker-body {
        height: 1fr;
    }
    TreePickerScreen #picker-sidebar {
        width: 34;
        border-right: solid $panel;
        padding: 0 1;
    }
    TreePickerScreen #picker-sidebar #picker-search {
        margin-bottom: 1;
    }
    TreePickerScreen #picker-sidebar #picker-sort {
        color: $text-muted;
        margin-bottom: 1;
    }
    TreePickerScreen #picker-sidebar .facet-box {
        height: auto;
        margin-bottom: 1;
    }
    TreePickerScreen #picker-sidebar .facet-title {
        color: $text-muted;
    }
    TreePickerScreen #picker-sidebar SelectionList {
        height: auto;
        max-height: 8;
    }
    TreePickerScreen #picker-main {
        width: 1fr;
    }
    TreePickerScreen #picker-status {
        dock: top;
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("j", "move_down", "Next", show=False),
        Binding("k", "move_up", "Prev", show=False),
        Binding("down", "move_down", "Next", show=False),
        Binding("up", "move_up", "Prev", show=False),
        Binding("enter", "select", "Open"),
        Binding("slash", "focus_search", "Search"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("c", "clear_filters", "Clear"),
        Binding("d", "delete_selected", "Delete"),
        Binding("escape", "cancel", "Back"),
        Binding("q", "cancel", "Back", show=False),
    ]

    def __init__(self, store: GenerationStore, current_root_id: str) -> None:
        super().__init__()
        self._store = store
        self._current_root_id = current_root_id
        # The model is owned by the app so filters persist across openings;
        # it is bound in on_mount once self.app is available.
        self._model = PickerModel()
        self._backend = get_backend(store)

    def compose(self) -> ComposeResult:
        with Horizontal(id="picker-body"):
            yield FacetSidebar(id="picker-sidebar")
            with Vertical(id="picker-main"):
                yield Static("", id="picker-status")
                yield TreePickerView(id="tree-list")
        yield Footer()

    def on_mount(self) -> None:
        # Reuse the app's persistent model so previously activated facet / sort /
        # search filters are still in effect when the picker is reopened.
        self._model = self.app.picker_model
        try:
            self._current_root_id = self._store.root(self._current_root_id).id
        except KeyError:
            pass
        self.query_one(TreePickerView).set_current_root_id(self._current_root_id)
        self._reload(focus_root_id=self._current_root_id)
        self._restore_search_box()
        self.query_one(TreePickerView).focus()

    # --- data flow ---

    def _reload(self, *, focus_root_id: str | None = None) -> None:
        """Rebuild entries from the store and repopulate sidebar + list."""
        self._model.set_entries(build_entries(self._store))
        status = self._backend.status()
        # Refresh a remembered keyword search against the current trees.
        if self._model.query and status.keyword:
            hits = self._backend.search(self._model.query)
            self._model.set_query(
                self._model.query, {hit.tree_id: hit.score for hit in hits}
            )
        self.query_one(FacetSidebar).populate(
            self._model, keyword=status.keyword, message=status.message
        )
        self._refresh_list(focus_root_id=focus_root_id)
        self._update_sort_label()

    def _restore_search_box(self) -> None:
        """Reflect a remembered query / text filter in the search input."""
        search = self.query_one("#picker-search", Input)
        value = self._model.query or self._model.text_filter
        if search.value != value:
            with search.prevent(Input.Changed):
                search.value = value

    def _refresh_list(self, *, focus_root_id: str | None = None) -> None:
        view = self.query_one(TreePickerView)
        view.set_entries(
            self._model.visible(),
            relevance=self._model.sort_mode == "relevance",
            focus_root_id=focus_root_id,
        )
        self._update_status()

    def _update_status(self) -> None:
        view = self.query_one(TreePickerView)
        text = f"Trees ({view.visible_count()}/{self._model.total_count})"
        if self._model.filters_active:
            text += "  ·  filtered"
        self.query_one("#picker-status", Static).update(text)

    def _update_sort_label(self) -> None:
        self.query_one(FacetSidebar).set_sort_label(f"sort: {self._model.sort_mode}")

    # --- sidebar events ---

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "picker-search":
            return
        # Live substring narrowing; editing leaves relevance-search mode.
        self._model.set_text_filter(event.value)
        if self._model.ranking is not None:
            self._model.clear_query()
        self._refresh_list()
        self._update_sort_label()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "picker-search":
            return
        query = event.value.strip()
        if query and self._backend.status().keyword:
            hits = self._backend.search(query)
            ranking = {hit.tree_id: hit.score for hit in hits}
            self._model.set_text_filter("")
            self._model.set_query(query, ranking)
        else:
            self._model.clear_query()
        self._refresh_list()
        self._update_sort_label()
        self.query_one(TreePickerView).focus()

    def on_selection_list_selected_changed(
        self, event: SelectionList.SelectedChanged
    ) -> None:
        facet = facet_of_list_id(event.selection_list.id)
        if facet is None:
            return
        self._model.set_facet(facet, set(event.selection_list.selected))
        self._refresh_list()

    # --- actions ---

    def action_focus_search(self) -> None:
        self.query_one(FacetSidebar).focus_search()

    def action_cycle_sort(self) -> None:
        self._model.cycle_sort()
        self._refresh_list()
        self._update_sort_label()

    def action_clear_filters(self) -> None:
        self._model.clear_all()
        search = self.query_one("#picker-search", Input)
        with search.prevent(Input.Changed):
            search.value = ""
        status = self._backend.status()
        self.query_one(FacetSidebar).populate(
            self._model, keyword=status.keyword, message=status.message
        )
        self._refresh_list()
        self._update_sort_label()

    def action_move_down(self) -> None:
        self.query_one(TreePickerView).move(+1)

    def action_move_up(self) -> None:
        self.query_one(TreePickerView).move(-1)

    def action_select(self) -> None:
        self.dismiss(self.query_one(TreePickerView).selected_root_id())

    def action_delete_selected(self) -> None:
        view = self.query_one(TreePickerView)
        root_id = view.selected_root_id()
        if root_id is None:
            return

        remaining_root_ids = [
            candidate for candidate in view.root_ids() if candidate != root_id
        ]
        if root_id == self._current_root_id and not remaining_root_ids:
            self.notify(
                "Cannot delete the only open tree", severity="warning", timeout=2
            )
            return

        from ..screens.confirm import ConfirmScreen

        root = self._store.root(root_id)
        tree = self._store.tree_for_node(root.id)
        name = tree.name or root.id[:8]

        def after_confirm(confirmed: bool) -> None:
            if confirmed:
                self._delete_root(root_id, remaining_root_ids)

        self.app.push_screen(
            ConfirmScreen("Delete tree?", f"{name} ({root.id[:8]})"),
            after_confirm,
        )

    def _delete_root(self, root_id: str, remaining_root_ids: list[str]) -> None:
        replacement_root_id = (
            remaining_root_ids[0] if root_id == self._current_root_id else None
        )
        self._store.delete_tree(root_id)

        if replacement_root_id is not None:
            self.dismiss(replacement_root_id)
            return

        self._reload(focus_root_id=self._current_root_id)
        self.notify("Tree deleted", timeout=2)

    def action_cancel(self) -> None:
        self.dismiss(None)
