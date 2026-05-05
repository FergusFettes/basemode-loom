from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from rich.console import Group
from rich.json import JSON
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ...store import GenerationStore, Node, Tree


class ConfigReviewScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("C", "close", "Close", show=False),
        Binding("r", "toggle_raw", "Raw", show=False),
    ]

    def __init__(self, store: GenerationStore, root: Node) -> None:
        super().__init__()
        self._store = store
        self._root = root
        self._raw = False

    def compose(self) -> ComposeResult:
        with Vertical(id="config-review-panel"):
            yield Static("Tree Config", id="config-review-title")
            with VerticalScroll(id="config-review-scroll"):
                yield Static(
                    self._render_content(),
                    id="config-review-content",
                )
            yield Static("r raw/parsed  Esc/q/C close", id="config-review-help")

    def action_close(self) -> None:
        self.dismiss(None)

    def action_toggle_raw(self) -> None:
        self._raw = not self._raw
        self.query_one("#config-review-content", Static).update(self._render_content())

    def _render_content(self) -> Group | JSON:
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


def _config_review_renderable(root: Node, tree: Tree, context: Node | None) -> Group:
    summary = Table("Setting", "Value", show_header=False)
    summary.add_row("Root", root.id)
    summary.add_row("Tree", tree.id)
    summary.add_row("Name", tree.name or "")
    summary.add_row("Show model names", str(tree.show_model_names))
    summary.add_row("Rewind split tokens", str(tree.rewind_split_tokens))

    renderables: list[Any] = [summary]

    if context is not None and context.text:
        renderables.extend(
            [Text(""), Text("Context", style="bold"), Text(context.text)]
        )

    model_plan = tree.model_plan
    if isinstance(model_plan, list) and model_plan:
        table = Table(
            "Model",
            "Branches",
            "Tokens",
            "Temp",
            "Enabled",
            header_style="bold",
        )
        for entry in model_plan:
            if not isinstance(entry, Mapping):
                continue
            table.add_row(
                str(entry.get("model", "")),
                str(entry.get("n_branches", "")),
                str(entry.get("max_tokens", "")),
                str(entry.get("temperature", "")),
                str(entry.get("enabled", "")),
            )
        renderables.extend([Text(""), Text("Model Plan", style="bold"), table])

    metadata = {}
    if tree.metadata:
        metadata["tree"] = tree.metadata
    if root.metadata:
        metadata["root"] = root.metadata
    if metadata:
        renderables.extend(
            [
                Text(""),
                Text("Metadata", style="bold"),
                JSON.from_data(metadata),
            ]
        )

    return Group(*renderables)


def _tree_dict(tree: Tree) -> dict[str, Any]:
    return {
        "id": tree.id,
        "current_node_id": tree.current_node_id,
        "name": tree.name,
        "show_model_names": tree.show_model_names,
        "rewind_split_tokens": tree.rewind_split_tokens,
        "model_plan": tree.model_plan,
        "created_at": tree.created_at,
        "updated_at": tree.updated_at,
        "metadata": tree.metadata,
    }


def _node_dict(node: Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "tree_id": node.tree_id,
        "parent_id": node.parent_id,
        "kind": node.kind,
        "text": node.text,
        "context_id": node.context_id,
        "model": node.model,
        "strategy": node.strategy,
        "max_tokens": node.max_tokens,
        "temperature": node.temperature,
        "checked_out": node.checked_out,
        "created_at": node.created_at,
        "metadata": node.metadata,
    }
