from __future__ import annotations

import json
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

from ...store import Node

_CONFIG_KEYS = {
    "context",
    "show_model_names",
    "model_plan",
}


class ConfigReviewScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("C", "close", "Close", show=False),
        Binding("r", "toggle_raw", "Raw", show=False),
    ]

    def __init__(self, root: Node) -> None:
        super().__init__()
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
        if self._raw:
            return JSON(json.dumps(self._root.metadata, indent=2, sort_keys=True))
        return _config_review_renderable(self._root)


def _config_review_renderable(root: Node) -> Group:
    config = _metadata_config(root.metadata)
    summary = Table("Setting", "Value", show_header=False)
    summary.add_row("Root", root.id)
    summary.add_row("Name", str(root.metadata.get("name", "")))
    summary.add_row("Show model names", str(config.get("show_model_names", "")))

    renderables: list[Any] = [summary]

    context = config.get("context")
    if isinstance(context, str) and context:
        renderables.extend([Text(""), Text("Context", style="bold"), Text(context)])

    model_plan = config.get("model_plan")
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

    extra_metadata = {
        key: value
        for key, value in root.metadata.items()
        if key != "config" and key not in _CONFIG_KEYS
    }
    if extra_metadata:
        renderables.extend(
            [
                Text(""),
                Text("Other Root Metadata", style="bold"),
                JSON.from_data(extra_metadata),
            ]
        )

    return Group(*renderables)


def _metadata_config(metadata: dict[str, Any]) -> dict[str, Any]:
    config = metadata.get("config")
    if isinstance(config, dict):
        return {key: config[key] for key in _CONFIG_KEYS if key in config}
    return {}
