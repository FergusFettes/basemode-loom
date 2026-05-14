from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static, TabbedContent, TabPane, Tabs

from rich.style import Style
from rich.text import Text

from ...session import PromptEntry

_ROLE_STYLES: dict[str, Style] = {
    "system": Style(color="cyan", bold=True),
    "user": Style(color="green", bold=True),
    "assistant": Style(color="yellow", bold=True),
}

_ROLE_LABELS = {"system": "SYSTEM", "user": "USER", "assistant": "ASSISTANT"}


def _render_entry(entry: PromptEntry) -> Text:
    result = Text(no_wrap=False, overflow="fold")
    result.append(f"strategy: {entry.strategy}\n", style=Style(dim=True))
    result.append("\n")
    if entry.messages is not None:
        for role, content in entry.messages:
            label = _ROLE_LABELS.get(role, role.upper())
            style = _ROLE_STYLES.get(role, Style(bold=True))
            result.append(f"── {label} ", style=style)
            result.append("─" * max(0, 60 - len(label) - 4) + "\n", style=Style(dim=True))
            result.append(content + "\n\n")
    else:
        result.append("── PROMPT ", style=Style(bold=True))
        result.append("─" * 50 + "\n", style=Style(dim=True))
        result.append(entry.prefix + "\n")
    return result


class PromptScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("p", "close", "Close", show=False),
        Binding("tab", "next_tab", "Next model", show=False),
    ]

    DEFAULT_CSS = """
    PromptScreen {
        align: center middle;
    }
    #prompt-panel {
        width: 90%;
        height: 90%;
        border: solid $primary;
        background: $surface;
    }
    #prompt-panel TabbedContent {
        height: 1fr;
    }
    #prompt-panel TabPane {
        height: 1fr;
        padding: 0 1;
    }
    #prompt-panel VerticalScroll {
        height: 1fr;
    }
    #prompt-footer {
        height: 1;
        dock: bottom;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, entries: tuple[PromptEntry, ...]) -> None:
        super().__init__()
        self._entries = entries

    def compose(self) -> ComposeResult:
        with TabbedContent(id="prompt-tabs"):
            if not self._entries:
                with TabPane("(no models)", id="tab-none"):
                    yield Static("No enabled models in plan.")
            else:
                for i, entry in enumerate(self._entries):
                    short = entry.model.split("/")[-1]
                    with TabPane(short, id=f"tab-{i}"):
                        with VerticalScroll():
                            yield Static(_render_entry(entry))
        yield Static("tab: next model  Esc/q/p: close", id="prompt-footer")

    def action_close(self) -> None:
        self.dismiss(None)

    def action_next_tab(self) -> None:
        self.query_one(Tabs).action_next_tab()
