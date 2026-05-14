from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from ...display import DisplayLine, build_loom_display, build_tree_display
from ...session import SessionState

_STYLES = {
    "normal": Style(),
    "bold": Style(bold=True),
    "dim": Style(dim=True),
    "path": Style(color="cyan", bold=True),
    "current": Style(color="black", bgcolor="cyan", bold=True),
    "selected": Style(color="yellow", bold=True),
    "model": Style(color="magenta", bold=True),
    "context": Style(color="green", bold=True),
}


class LoomView(VerticalScroll):
    """Renders the loom tree: parent text, selected child (bold), siblings (dim),
    and the continuation path below the selection."""

    DEFAULT_CSS = """
    LoomView {
        height: 1fr;
    }
    LoomView Static {
        width: 100%;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._cursor_char_end: int | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="loom-content")

    def update_state(self, state: SessionState) -> None:
        self._state = state
        self._cursor_char_end = None
        self._render_state()

    def set_cursor(self, char_end: int | None) -> None:
        """Set or clear the word cursor within the selected child's text."""
        self._cursor_char_end = char_end
        self._render_state()

    def _render_state(self) -> None:
        state = getattr(self, "_state", None)
        if state is None:
            return
        width = self._content_width()
        if state.view_mode == "tree":
            lines = build_tree_display(state, width)
        else:
            lines = build_loom_display(state, width, child_cursor=self._cursor_char_end)
        result = Text(no_wrap=True, overflow="fold")
        for line in lines:
            text = Text(line.text + "\n", style=_STYLES[line.style])
            for span in line.spans:
                text.stylize(_STYLES[span.style], span.start, span.end)
            result.append_text(text)
        self.query_one("#loom-content", Static).update(result)
        if state.view_mode == "tree":
            self.scroll_to(y=self._tree_scroll_target(lines), animate=False)
        else:
            self.scroll_end(animate=False)

    def _tree_scroll_target(self, lines: list[DisplayLine]) -> int:
        for index, line in enumerate(lines):
            if line.style == "selected":
                return max(0, index - 3)
        for index, line in enumerate(lines):
            if line.style == "current":
                return max(0, index - 3)
        return 0

    def _content_width(self) -> int:
        width = self.scrollable_content_region.width
        if width >= 20:
            return width
        width = self.scrollable_size.width
        if width >= 20:
            return width
        try:
            app_width = self.app.size.width
        except Exception:
            app_width = 0
        return app_width if app_width >= 20 else 80

    def on_resize(self, event: events.Resize) -> None:
        self._render_state()
