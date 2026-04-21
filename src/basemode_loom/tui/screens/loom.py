from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar

from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import ContentSwitcher, Static

from ...config import DEFAULT_CONFIG, Config, KeyMap
from ...session import (
    GenerationCancelled,
    GenerationComplete,
    GenerationError,
    LoomSession,
    TokenReceived,
)
from ..widgets.loom_view import LoomView
from ..widgets.stream_view import StreamView

# Maps shift+number characters (US layout) to their digit values.
_SHIFT_DIGITS: dict[str, int] = {
    "!": 1, "@": 2, "#": 3, "$": 4, "%": 5,
    "^": 6, "&": 7, "*": 8, "(": 9,
}


def _build_bindings(km: KeyMap = DEFAULT_CONFIG.keys) -> list[Binding]:
    return [
        Binding(km.nav_parent, "nav_parent", "Parent", show=False),
        Binding(km.nav_child, "nav_child", "Child", show=False),
        Binding(km.nav_next, "nav_next", "Next", show=False),
        Binding(km.nav_prev, "nav_prev", "Prev", show=False),
        Binding(km.word_prev, "word_prev", "◀word", show=False),
        Binding(km.word_next, "word_next", "word▶", show=False),
        Binding(km.generate, "generate", "Generate"),
        Binding(km.quick_generate, "quick_generate", "Quick gen", show=False),
        Binding(km.edit, "edit", "Edit"),
        Binding(km.edit_context, "edit_context", "Context", show=False),
        Binding(km.pick_model, "pick_model", "Model"),
        Binding(km.tokens_up, "tokens_up", "+tok", show=False),
        Binding(km.tokens_down, "tokens_down", "-tok", show=False),
        Binding(km.set_tokens, "set_tokens", "Tokens"),
        Binding(km.branches_up, "branches_up", "+n", show=False),
        Binding(km.branches_down, "branches_down", "-n", show=False),
        Binding(km.toggle_tree_view, "toggle_tree_view", "Tree"),
        Binding(km.toggle_model_names, "toggle_model_names", "Names", show=False),
        Binding(km.toggle_hoist, "toggle_hoist", "Hoist", show=False),
        Binding(km.toggle_bookmark, "toggle_bookmark", "Bookmark", show=False),
        Binding(km.next_bookmark, "next_bookmark", "Next mark", show=False),
        Binding(km.open_picker, "open_picker", "Trees"),
        Binding(km.open_stats, "open_stats", "Stats"),
        Binding(km.quit, "quit", "Quit"),
        Binding(km.cancel_or_quit, "cancel_or_quit", "Cancel", show=False),
    ]


def _word_ends(text: str) -> list[int]:
    """Return the character position after each word in text."""
    return [m.end() for m in re.finditer(r"\S+", text)]


class LoomScreen(Screen):
    BINDINGS: ClassVar[list[Binding]] = _build_bindings(DEFAULT_CONFIG.keys)

    def __init__(self, session: LoomSession, config: Config = DEFAULT_CONFIG) -> None:
        super().__init__()
        self.session = session
        self.keymap = config.keys
        self._generating = False
        self._cursor_word_idx: int | None = None

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="loom", id="loom-switcher"):
            yield LoomView(id="loom")
            yield StreamView(id="stream")
        yield Static("", id="status-bar")

    def on_mount(self) -> None:
        self.query_one(LoomView).update_state(self.session.get_state())
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        s = self.session
        short_model = s.model.split("/")[-1]
        if self._cursor_word_idx is not None:
            km = self.keymap
            info = (
                f"{s._current_id[:8]} {short_model}  "
                f"CURSOR  {km.word_prev}: ◀word  {km.word_next}: ▶word  "
                f"{km.generate}: truncate+gen  {km.cancel_or_quit}: cancel"
            )
        else:
            info = (
                f"{s._current_id[:8]} {short_model}  "
                f"tokens:{s.max_tokens} branches:{s.n_branches}  "
                f"view:{s.view_mode}{' hoist' if s._hoisted_id else ''} "
                f"names:{'on' if s.show_model_names else 'off'}  "
                "hjkl nav  space gen  1-9: N branches  S+space: 10tok  "
                "e edit  v view  b mark  tab trees  ? stats  q quit"
            )
        self.sub_title = info
        self.query_one("#status-bar", Static).update(info)

    def _refresh(self) -> None:
        self._cursor_word_idx = None
        self.query_one(LoomView).update_state(self.session.get_state())
        self._update_subtitle()

    def _refresh_cursor(self) -> None:
        """Redraw with current cursor position within the selected child's text."""
        state = self.session.get_state()
        loom_view = self.query_one(LoomView)
        if self._cursor_word_idx is not None and state.children:
            child_text = state.children[state.selected_child_idx].text
            ends = _word_ends(child_text)
            if ends and self._cursor_word_idx < len(ends):
                loom_view.set_cursor(ends[self._cursor_word_idx])
            else:
                loom_view.set_cursor(None)
        else:
            loom_view.set_cursor(None)
        self._update_subtitle()

    # --- Navigation ---

    def action_nav_child(self) -> None:
        state = self.session.get_state()
        if not state.children:
            self.notify(
                "No continuations yet — press space to generate", timeout=2
            )
            return
        self.session.navigate_child()
        self._refresh()

    def action_nav_parent(self) -> None:
        state = self.session.get_state()
        if state.current_node.parent_id is None:
            self.notify("Already at root", timeout=1)
            return
        self.session.navigate_parent()
        self._refresh()

    def action_nav_next(self) -> None:
        state = self.session.get_state()
        if not state.children:
            return
        self.session.select_sibling(+1)
        self._refresh()

    def action_nav_prev(self) -> None:
        state = self.session.get_state()
        if not state.children:
            return
        self.session.select_sibling(-1)
        self._refresh()

    # --- Word cursor ---

    def _child_word_ends(self) -> list[int]:
        state = self.session.get_state()
        if not state.children:
            return []
        return _word_ends(state.children[state.selected_child_idx].text)

    def action_word_prev(self) -> None:
        ends = self._child_word_ends()
        if not ends:
            return
        if self._cursor_word_idx is None:
            new_idx = len(ends) - 2
        else:
            new_idx = self._cursor_word_idx - 1
        if new_idx < 0:
            return
        self._cursor_word_idx = new_idx
        self._refresh_cursor()

    def action_word_next(self) -> None:
        if self._cursor_word_idx is None:
            return
        ends = self._child_word_ends()
        if not ends:
            return
        new_idx = self._cursor_word_idx + 1
        if new_idx >= len(ends) - 1:
            self._cursor_word_idx = None
            self.query_one(LoomView).set_cursor(None)
        else:
            self._cursor_word_idx = new_idx
            self.query_one(LoomView).set_cursor(ends[new_idx])
        self._update_subtitle()

    # --- View ---

    def action_toggle_tree_view(self) -> None:
        self.session.toggle_tree_view()
        self._refresh()

    def action_toggle_model_names(self) -> None:
        self.session.toggle_model_names()
        self._refresh()

    def action_toggle_hoist(self) -> None:
        self.session.toggle_hoist()
        self._refresh()

    def action_toggle_bookmark(self) -> None:
        bookmarked = self.session.toggle_bookmark()
        self.notify("Bookmarked" if bookmarked else "Bookmark removed", timeout=1)
        self._refresh()

    def action_next_bookmark(self) -> None:
        before = self.session.get_state().current_node_id
        self.session.next_bookmark()
        after = self.session.get_state().current_node_id
        if after == before:
            self.notify("No bookmarks", timeout=1)
        self._refresh()

    # --- Params ---

    def action_tokens_up(self) -> None:
        self.session.set_max_tokens(self.session.max_tokens + 50)
        self._update_subtitle()

    def action_tokens_down(self) -> None:
        self.session.set_max_tokens(self.session.max_tokens - 50)
        self._update_subtitle()

    def action_branches_up(self) -> None:
        self.session.set_n_branches(self.session.n_branches + 1)
        self._update_subtitle()

    def action_branches_down(self) -> None:
        self.session.set_n_branches(self.session.n_branches - 1)
        self._update_subtitle()

    def action_set_tokens(self) -> None:
        from ..screens.int_input import IntInputScreen

        def apply(result: int | None) -> None:
            if result is not None and result > 0:
                self.session.set_max_tokens(result)
                self._update_subtitle()

        self.app.push_screen(
            IntInputScreen("Max tokens", self.session.max_tokens), apply
        )

    def action_pick_model(self) -> None:
        from ..screens.model_picker import ModelPickerScreen

        def apply(result: str | None) -> None:
            if result is not None:
                self.session.set_model(result)
                self._update_subtitle()

        self.app.push_screen(ModelPickerScreen(self.session.model), apply)

    # --- Edit / context ---

    async def action_edit(self) -> None:
        state = self.session.get_state()
        original = state.full_text
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(original)
            tmpfile = f.name
        with self.app.suspend():
            subprocess.run([os.environ.get("EDITOR", "vim"), tmpfile])
        edited = Path(tmpfile).read_text().rstrip("\n")
        Path(tmpfile).unlink(missing_ok=True)
        self.session.apply_edit(original, edited)
        self._refresh()

    async def action_edit_context(self) -> None:
        state = self.session.get_state()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(state.context)
            tmpfile = f.name
        with self.app.suspend():
            subprocess.run([os.environ.get("EDITOR", "vim"), tmpfile])
        new_context = Path(tmpfile).read_text().rstrip("\n")
        Path(tmpfile).unlink(missing_ok=True)
        self.session.update_context(new_context)

    # --- Tree picker ---

    def action_open_picker(self) -> None:
        if self._generating:
            return
        from ..screens.tree_picker import TreePickerScreen

        state = self.session.get_state()

        def on_selected(root_id: str | None) -> None:
            if root_id is None or root_id == state.root_id:
                return
            if self.session.store.get(state.root_id) is not None:
                self.session.save()
            self.session = LoomSession(self.session.store, root_id)
            self.app.session = self.session
            self._refresh()

        self.app.push_screen(
            TreePickerScreen(self.session.store, state.root_id), on_selected
        )

    # --- Stats ---

    def action_open_stats(self) -> None:
        if self._generating:
            return
        from ...stats import analyze_tree
        from ..screens.stats import StatsScreen

        state = self.session.get_state()
        stats = analyze_tree(
            self.session.store,
            state.root_id,
            path_node_id=state.current_node_id,
        )
        self.app.push_screen(StatsScreen(stats))

    # --- Quit / cancel ---

    def action_cancel_or_quit(self) -> None:
        if self._generating:
            self.session.cancel()
        elif self._cursor_word_idx is not None:
            self._cursor_word_idx = None
            self.query_one(LoomView).set_cursor(None)
            self._update_subtitle()
        else:
            self.app.exit(message=self._quit_message())

    def action_quit(self) -> None:
        self.app.exit(message=self._quit_message())

    def _quit_message(self) -> str:
        state = self.session.get_state()
        root = self.session.store.root(state.root_id)
        name = root.metadata.get("name") or root.id[:8]
        return f"Quit tree: {name} ({root.id})\nRejoin: basemode-loom view {root.id}"

    # --- Generation ---

    def action_generate(self) -> None:
        self._generate_worker()

    def action_quick_generate(self) -> None:
        self._generate_worker(max_tokens=10)

    def on_key(self, event: events.Key) -> None:
        if self._generating:
            return
        char = event.character or ""
        if char.isdigit() and char != "0":
            event.stop()
            self._generate_worker(n_branches=int(char))
        elif char in _SHIFT_DIGITS:
            event.stop()
            self._generate_worker(n_branches=_SHIFT_DIGITS[char], max_tokens=10)

    @work(exclusive=True)
    async def _generate_worker(
        self,
        n_branches: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        if self._cursor_word_idx is not None:
            state = self.session.get_state()
            if state.children:
                child_text = state.children[state.selected_child_idx].text
                ends = _word_ends(child_text)
                if ends and self._cursor_word_idx < len(ends):
                    self.session.truncate_selected_child(ends[self._cursor_word_idx])
            self._cursor_word_idx = None

        old_n = self.session.n_branches
        old_tok = self.session.max_tokens
        if n_branches is not None:
            self.session.set_n_branches(n_branches)
        if max_tokens is not None:
            self.session.set_max_tokens(max_tokens)

        state = self.session.get_state()
        stream_view = self.query_one(StreamView)
        stream_view.reset(self.session.n_branches, state.full_text)
        self.query_one(ContentSwitcher).current = "stream"
        self._generating = True

        try:
            async for event in self.session.generate():
                match event:
                    case TokenReceived(slot_idx=idx, token=tok):
                        stream_view.add_token(idx, tok)
                    case GenerationComplete():
                        pass
                    case GenerationCancelled():
                        pass
                    case GenerationError(error=exc):
                        self.notify(str(exc), severity="error")
        finally:
            self._generating = False
            if n_branches is not None:
                self.session.set_n_branches(old_n)
            if max_tokens is not None:
                self.session.set_max_tokens(old_tok)
            self.query_one(ContentSwitcher).current = "loom"
            self._refresh()
