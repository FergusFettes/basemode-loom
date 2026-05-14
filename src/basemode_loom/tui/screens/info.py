from __future__ import annotations

import dataclasses
from typing import Any, ClassVar

from rich.console import Group
from rich.json import JSON
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static, TabbedContent, TabPane, Tabs

from ...config import KeyMap
from ...stats import LoomStats
from ...store import GenerationStore, Node
from .config_review import (
    _config_review_renderable,
    _node_dict,
    _tree_dict,
)
from .stats import _stats_renderable


_KEY_DESCRIPTIONS: dict[str, str] = {
    "nav_parent": "Navigate to parent",
    "nav_child": "Navigate to child",
    "nav_next": "Next sibling",
    "nav_prev": "Previous sibling",
    "word_prev": "Move cursor left one word",
    "word_next": "Move cursor right one word",
    "generate": "Generate continuations",
    "quick_generate": "Quick generate (single branch)",
    "numeric_branch_shortcuts": None,  # not a key
    "edit": "Edit current node",
    "edit_full": "Full edit (external editor)",
    "edit_context": "Edit context",
    "pick_model": "Pick model",
    "tokens_up": "Increase max tokens",
    "tokens_down": "Decrease max tokens",
    "set_tokens": "Set max tokens",
    "branches_up": "Increase branches",
    "branches_down": "Decrease branches",
    "toggle_tree_view": "Cycle view (branch / tree / prompt)",
    "toggle_model_names": "Toggle model name display",
    "toggle_hoist": "Hoist current node",
    "toggle_bookmark": "Toggle bookmark",
    "next_bookmark": "Jump to next bookmark",
    "open_picker": "Open tree picker",
    "open_stats": "Open info screen",
    "open_config_review": "Open info screen (config tab)",
    "open_prompt": "Open prompt literal view",
    "quit": "Quit",
    "cancel_or_quit": "Cancel / quit",
}


class InfoScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("question_mark", "close", "Close", show=False),
        Binding("tab", "next_tab", "Next tab", show=False),
        Binding("r", "toggle_raw", "Raw", show=False),
    ]

    DEFAULT_CSS = """
    InfoScreen {
        align: center middle;
    }
    #info-panel {
        width: 90%;
        height: 90%;
        border: solid $primary;
        background: $surface;
    }
    #info-panel TabbedContent {
        height: 1fr;
    }
    #info-panel TabPane {
        height: 1fr;
        padding: 0 1;
    }
    #info-panel VerticalScroll {
        height: 1fr;
    }
    #info-footer {
        height: 1;
        dock: bottom;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        stats: LoomStats,
        store: GenerationStore,
        root: Node,
        keymap: KeyMap,
        initial_tab: str = "tab-keys",
    ) -> None:
        super().__init__()
        self._stats = stats
        self._store = store
        self._root = root
        self._keymap = keymap
        self._initial_tab = initial_tab
        self._raw = False

    def compose(self) -> ComposeResult:
        with TabbedContent(initial=self._initial_tab, id="info-tabs"):
            with TabPane("Keys", id="tab-keys"):
                with VerticalScroll():
                    yield Static(_keys_renderable(self._keymap), id="keys-content")
            with TabPane("Config", id="tab-config"):
                with VerticalScroll():
                    yield Static(self._render_config(), id="config-content")
            with TabPane("Stats", id="tab-stats"):
                with VerticalScroll():
                    yield Static(_stats_renderable(self._stats), id="stats-content")
        yield Static("tab: next  r: raw config  Esc/q: close", id="info-footer")

    def action_close(self) -> None:
        self.dismiss(None)

    def action_next_tab(self) -> None:
        self.query_one(Tabs).action_next_tab()

    def action_toggle_raw(self) -> None:
        tc = self.query_one(TabbedContent)
        if tc.active != "tab-config":
            return
        self._raw = not self._raw
        self.query_one("#config-content", Static).update(self._render_config())

    def _render_config(self) -> Group | JSON:
        tree = self._store.tree_for_node(self._root.id)
        context = self._context_node()
        if self._raw:
            return JSON.from_data(
                {
                    "tree": _tree_dict(tree),
                    "root": _node_dict(self._root),
                    "context": _node_dict(context) if context else None,
                }
            )
        return _config_review_renderable(self._root, tree, context)

    def _context_node(self) -> Node | None:
        if self._root.context_id is None:
            return None
        context = self._store.get(self._root.context_id)
        if context is None or context.kind != "context":
            return None
        return context


def _keys_renderable(keymap: KeyMap) -> Table:
    table = Table("Action", "Key", show_header=True, header_style="bold")
    for f in dataclasses.fields(keymap):
        desc = _KEY_DESCRIPTIONS.get(f.name)
        if desc is None:
            continue
        value = getattr(keymap, f.name)
        table.add_row(desc, str(value))
    return table
