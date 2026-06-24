"""Tests for the tree picker view and screen."""

import pytest

from basemode_loom.session import LoomSession
from basemode_loom.store import GenerationStore
from basemode_loom.tui.app import BasemodeApp
from basemode_loom.tui.screens.confirm import ConfirmScreen
from basemode_loom.tui.screens.loom import LoomScreen
from basemode_loom.tui.screens.tree_picker import TreePickerScreen
from basemode_loom.tui.widgets.facet_sidebar import FacetSidebar
from basemode_loom.tui.widgets.tree_picker import (
    _ENTRY_HEIGHT,
    TreePickerView,
    build_entries,
)


def make_view(store, current_root_id=""):
    """Build a TreePickerView populated like the screen does (no filters)."""
    try:
        root_id = store.root(current_root_id).id
    except KeyError:
        root_id = current_root_id
    view = TreePickerView()
    view.set_current_root_id(root_id)
    view.set_entries(build_entries(store), focus_root_id=root_id or None)
    return view


@pytest.fixture
def store(tmp_path):
    return GenerationStore(tmp_path / "test.sqlite")


@pytest.fixture
def multi_tree_store(tmp_path):
    """Three separate trees in the store."""
    store = GenerationStore(tmp_path / "test.sqlite")
    _, ab = store.save_continuations(
        "The ship rounded the headland",
        ["and sailed on", "and turned back"],
        model="m",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    store.update_tree_settings(ab[0].tree_id, name="ship-story")

    _, cd = store.save_continuations(
        "Once upon a time",
        ["there was a dragon"],
        model="m",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    store.update_tree_settings(cd[0].tree_id, name="fairy-tale")

    _, ef = store.save_continuations(
        "The quick brown fox",
        ["jumped over"],
        model="m",
        strategy="system",
        max_tokens=20,
        temperature=0.9,
    )
    # third tree intentionally unnamed

    return store, ab, cd, ef


@pytest.fixture
def many_tree_store(tmp_path):
    """Enough trees for the current tree to start outside the first viewport."""
    store = GenerationStore(tmp_path / "test.sqlite")
    first_children = None
    for i in range(30):
        _, children = store.save_continuations(
            f"Tree {i:02d} root",
            [f" child {i:02d}"],
            model="m",
            strategy="system",
            max_tokens=20,
            temperature=0.9,
        )
        store.update_tree_settings(children[0].tree_id, name=f"tree-{i:02d}")
        if first_children is None:
            first_children = children
    assert first_children is not None
    return store, first_children


@pytest.fixture
def varied_store(tmp_path):
    """Two trees with distinct sources, models, sizes, and creation order."""
    store = GenerationStore(tmp_path / "varied.sqlite")
    # Tree A: created first, source codex, model gpt-5, small.
    a = store.create_root("alpha root", metadata={"source": "codex"})
    store.add_child(
        a.id, " a1", model="openai/gpt-5", strategy="s",
        max_tokens=10, temperature=0.9, metadata={"source": "codex"},
    )
    store.update_tree_settings(
        a.tree_id, metadata={"category": "code", "domain": "agent"}
    )
    # Tree B: created later, source claude, model claude-opus, larger.
    b = store.create_root("beta root", metadata={"source": "claude"})
    parent = b.id
    for i in range(3):
        child = store.add_child(
            parent, f" b{i}", model="anthropic/claude-opus-4-8", strategy="s",
            max_tokens=10, temperature=0.9, metadata={"source": "claude"},
        )
        parent = child.id
    store.update_tree_settings(b.tree_id, name="beta")
    store.update_tree_settings(
        b.tree_id, metadata={"category": "literature", "domain": "chat"}
    )
    return store, a, b


# --- classification / facets in entries ---


def test_build_entries_reads_category_and_domain(varied_store):
    store, a, b = varied_store
    entries = {e.root.id: e for e in build_entries(store)}
    assert entries[a.id].category == "code"
    assert entries[a.id].domain == "agent"
    assert entries[b.id].category == "literature"


def test_meta_line_shows_category_and_domain(varied_store):
    store, a, b = varied_store
    view = make_view(store, a.id)
    rendered = str(view.render())
    assert "code" in rendered
    assert "literature" in rendered


# --- TreePickerView unit tests ---


def test_tree_picker_view_loads_all_trees(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, ab[0].id)
    assert len(view._entries) == 3


def test_tree_picker_view_cursor_starts_on_current_tree(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, cd[0].id)
    assert view.selected_root_id() == store.root(cd[0].id).id


def test_tree_picker_view_move_down(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, ab[0].id)
    view._cursor = 0
    view.move(+1)
    assert view._cursor == 1


def test_tree_picker_view_move_up(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, ab[0].id)
    view._cursor = 1
    view.move(-1)
    assert view._cursor == 0


def test_tree_picker_view_move_clamps_at_zero(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, ab[0].id)
    view._cursor = 0
    view.move(-1)
    assert view._cursor == 0


def test_tree_picker_view_move_clamps_at_max(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, ab[0].id)
    view._cursor = len(view._entries) - 1
    view.move(+1)
    assert view._cursor == len(view._entries) - 1


def test_tree_picker_view_node_count_includes_root(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, ab[0].id)
    # ab has root + 2 children = 3 nodes
    ship_entry = next(e for e in view._entries if e.name == "ship-story")
    assert ship_entry.node_count == 3


def test_tree_picker_view_root_preview_text(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, ab[0].id)
    ship_entry = next(e for e in view._entries if e.name == "ship-story")
    assert "ship" in ship_entry.root_preview.lower()


def test_tree_picker_view_root_preview_not_fixed_width_truncated(store):
    root = store.create_root(" ".join(f"word{i}" for i in range(40)))
    view = make_view(store, root.id)
    assert len(view._entries[0].root_preview) > 72


def test_tree_picker_shows_source_and_players(store):
    root = store.create_root("hello", metadata={"role": "user", "source": "codex"})
    store.add_child(
        root.id,
        "hi there",
        model="anthropic/claude-3-7-sonnet",
        strategy="system",
        max_tokens=10,
        temperature=0.9,
        metadata={"role": "assistant", "source": "codex"},
    )
    view = make_view(store, root.id)
    entry = view._entries[0]
    assert entry.source == "codex"
    assert "claude-3-7-sonnet" in entry.players  # short name, no provider prefix
    rendered = str(view.render())
    assert "codex" in rendered
    assert "claude-3-7-sonnet" in rendered


def test_tree_picker_source_players_blank_when_absent(multi_tree_store):
    # save_continuations sets no source metadata; model is the bare "m"
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, ab[0].id)
    ship = next(e for e in view._entries if e.name == "ship-story")
    assert ship.source == ""
    assert ship.players == "m"


def test_tree_picker_view_render_shows_delete_per_item(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, ab[0].id)
    rendered = str(view.render())
    assert "[d delete]" in rendered
    assert "[delete]" in rendered


def test_tree_picker_view_leaf_preview_none_when_only_root(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, ab[0].id)
    fairy_entry = next(e for e in view._entries if e.name == "fairy-tale")
    # No current_node_id set yet on this tree
    assert fairy_entry.leaf_preview == "(at root)"


def test_tree_picker_view_leaf_preview_uses_current_node_id(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    # Save a current_node_id pointing to one of ab's children
    store.set_active_node(ab[0].id)
    view = make_view(store, ab[0].id)
    ship_entry = next(e for e in view._entries if e.name == "ship-story")
    assert ship_entry.leaf_preview != "(at root)"


def test_tree_picker_view_unnamed_tree_uses_id_prefix(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, ab[0].id)
    unnamed = next(e for e in view._entries if not e.name)
    # Should display first 8 chars of node id
    assert len(unnamed.root.id[:8]) == 8


def test_tree_picker_view_render_no_crash(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    view = make_view(store, ab[0].id)
    view._size = type("S", (), {"width": 80, "height": 24})()  # fake size
    from rich.text import Text

    result = view.render()
    assert isinstance(result, Text)


def test_tree_picker_view_empty_store_renders_no_trees(store):
    view = make_view(store, "nonexistent")
    result = view.render()
    assert "No trees" in str(result)


# --- TreePickerScreen pilot tests ---


@pytest.mark.asyncio
async def test_tab_opens_tree_picker(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        await pilot.press("tab")
        assert isinstance(app.screen, TreePickerScreen)


@pytest.mark.asyncio
async def test_picker_scrolls_current_tree_into_view_on_open(many_tree_store):
    store, children = many_tree_store
    session = LoomSession(store, children[0].id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True, size=(80, 24)) as pilot:
        await pilot.press("tab")
        await pilot.pause()

        view = app.screen.query_one(TreePickerView)
        selected_y = view._cursor * _ENTRY_HEIGHT

        assert view._cursor > 0
        assert view.scroll_y <= selected_y
        assert selected_y < view.scroll_y + view.scrollable_content_region.height


@pytest.mark.asyncio
async def test_picker_rendered_content_tracks_scroll_position(many_tree_store):
    store, children = many_tree_store
    session = LoomSession(store, children[0].id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True, size=(80, 24)) as pilot:
        await pilot.press("tab")
        await pilot.pause()

        view = app.screen.query_one(TreePickerView)
        top_line = view.render_line(0).text
        selected_line = view.render_line(
            view._cursor * _ENTRY_HEIGHT - int(view.scroll_y)
        ).text

        assert "tree-29" not in selected_line
        assert "tree-00" in selected_line
        assert top_line != view.render().plain.splitlines()[0]


@pytest.mark.asyncio
async def test_picker_s_cycles_sort(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        await pilot.press("tab")
        assert app.screen._model.sort_mode == "recent"
        await pilot.press("s")
        assert app.screen._model.sort_mode == "oldest"


@pytest.mark.asyncio
async def test_picker_slash_focuses_search(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        assert app.screen.focused is not None
        assert app.screen.focused.id == "picker-search"


@pytest.mark.asyncio
async def test_picker_escape_returns_to_loom(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        await pilot.press("tab")
        await pilot.press("escape")
        assert isinstance(app.screen, LoomScreen)


@pytest.mark.asyncio
async def test_picker_q_returns_to_loom(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        await pilot.press("tab")
        await pilot.press("q")
        assert isinstance(app.screen, LoomScreen)


@pytest.mark.asyncio
async def test_picker_jk_navigates(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        await pilot.press("tab")
        picker_view = app.screen.query_one(TreePickerView)
        before = picker_view._cursor
        if before < len(picker_view._entries) - 1:
            await pilot.press("j")
            assert picker_view._cursor == before + 1
            await pilot.press("k")
        else:
            await pilot.press("k")
            assert picker_view._cursor == before - 1
            await pilot.press("j")
        assert picker_view._cursor == before


@pytest.mark.asyncio
async def test_picker_enter_same_tree_no_change(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    original_root = session.get_state().root_id
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        await pilot.press("tab")
        # Cursor should already be on the current tree; pressing enter stays
        await pilot.press("enter")
        assert isinstance(app.screen, LoomScreen)
        # Session root unchanged since we selected the same tree
        assert session.get_state().root_id == original_root


@pytest.mark.asyncio
async def test_picker_enter_different_tree_switches_session(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    # Start on the ship-story tree
    session = LoomSession(store, ab[0].id)
    original_root = session.get_state().root_id
    app = BasemodeApp(session)

    # The loom_screen holds session; we'll inspect via the screen after switch
    loom_screen: LoomScreen

    async with app.run_test(headless=True) as pilot:
        loom_screen = app.screen  # type: ignore[assignment]

        await pilot.press("tab")
        picker_view = app.screen.query_one(TreePickerView)

        # Find a different tree and its position
        target_idx = next(
            i for i, e in enumerate(picker_view._entries) if e.root.id != original_root
        )
        target_root_id = picker_view._entries[target_idx].root.id

        # Navigate to that entry via j/k from current cursor
        cur = picker_view._cursor
        key = "j" if target_idx > cur else "k"
        for _ in range(abs(target_idx - cur)):
            await pilot.press(key)

        await pilot.press("enter")
        assert isinstance(app.screen, LoomScreen)
        # The loom screen's session should now be on the new tree
        assert loom_screen.session.get_state().root_id == target_root_id


@pytest.mark.asyncio
async def test_picker_facet_toggle_filters_list(varied_store):
    store, a, b = varied_store
    session = LoomSession(store, a.id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True, size=(100, 30)) as pilot:
        await pilot.press("tab")
        screen = app.screen
        view = screen.query_one(TreePickerView)
        assert view.visible_count() == 2
        # Select the "literature" category via the model + sidebar event path.
        screen._model.toggle_facet("category", "literature")
        screen._refresh_list()
        assert view.visible_count() == 1
        assert view.selected_root_id() == b.id


@pytest.mark.asyncio
async def test_picker_remembers_filters_across_reopen(varied_store):
    store, a, b = varied_store
    session = LoomSession(store, a.id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True, size=(100, 30)) as pilot:
        await pilot.press("tab")
        first = app.screen
        first._model.toggle_facet("category", "literature")
        first._model.cycle_sort()  # recent -> oldest
        first._refresh_list()
        assert first.query_one(TreePickerView).visible_count() == 1

        await pilot.press("escape")  # back to loom
        await pilot.pause()
        await pilot.press("tab")  # reopen picker
        await pilot.pause()

        second = app.screen
        assert second is not first
        assert second._model.active.get("category") == {"literature"}
        assert second._model.sort_mode == "oldest"
        assert second.query_one(TreePickerView).visible_count() == 1


@pytest.mark.asyncio
async def test_picker_sidebar_present_with_facets(varied_store):
    store, a, b = varied_store
    session = LoomSession(store, a.id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True, size=(100, 30)) as pilot:
        await pilot.press("tab")
        sidebar = app.screen.query_one(FacetSidebar)
        assert sidebar is not None
        assert app.screen._model.has_facet_values("category")


@pytest.mark.asyncio
async def test_picker_shows_tree_names(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        await pilot.press("tab")
        view = app.screen.query_one(TreePickerView)
        names = {e.name for e in view._entries}
        assert "ship-story" in names
        assert "fairy-tale" in names


@pytest.mark.asyncio
async def test_picker_shows_delete_affordance_in_items(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        await pilot.press("tab")
        rendered = str(app.screen.query_one(TreePickerView).render())
        assert "[d delete]" in rendered


@pytest.mark.asyncio
async def test_picker_delete_opens_confirmation(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    current_root = session.get_state().root_id
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        await pilot.press("tab")
        view = app.screen.query_one(TreePickerView)
        target_idx = next(
            i for i, e in enumerate(view._entries) if e.root.id != current_root
        )
        target_root_id = view._entries[target_idx].root.id

        cur = view._cursor
        key = "j" if target_idx > cur else "k"
        for _ in range(abs(target_idx - cur)):
            await pilot.press(key)

        await pilot.press("d")

        assert isinstance(app.screen, ConfirmScreen)
        assert store.get(target_root_id) is not None


@pytest.mark.asyncio
async def test_picker_delete_escape_cancels(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    current_root = session.get_state().root_id
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        await pilot.press("tab")
        view = app.screen.query_one(TreePickerView)
        target_idx = next(
            i for i, e in enumerate(view._entries) if e.root.id != current_root
        )
        target_root_id = view._entries[target_idx].root.id

        cur = view._cursor
        key = "j" if target_idx > cur else "k"
        for _ in range(abs(target_idx - cur)):
            await pilot.press(key)

        await pilot.press("d")
        await pilot.press("escape")

        assert isinstance(app.screen, TreePickerScreen)
        assert store.get(target_root_id) is not None


@pytest.mark.asyncio
async def test_picker_delete_enter_confirms_non_current_tree(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    current_root = session.get_state().root_id
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        await pilot.press("tab")
        view = app.screen.query_one(TreePickerView)
        target_idx = next(
            i for i, e in enumerate(view._entries) if e.root.id != current_root
        )
        target_root_id = view._entries[target_idx].root.id

        cur = view._cursor
        key = "j" if target_idx > cur else "k"
        for _ in range(abs(target_idx - cur)):
            await pilot.press(key)

        await pilot.press("d")
        await pilot.press("enter")

        assert isinstance(app.screen, TreePickerScreen)
        assert store.get(target_root_id) is None
        assert target_root_id not in view.root_ids()
        assert session.get_state().root_id == current_root


@pytest.mark.asyncio
async def test_picker_delete_current_tree_switches_to_remaining_tree(multi_tree_store):
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    original_root = session.get_state().root_id
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        loom_screen = app.screen
        await pilot.press("tab")

        await pilot.press("d")
        await pilot.press("enter")

        assert isinstance(app.screen, LoomScreen)
        assert store.get(original_root) is None
        assert loom_screen.session.get_state().root_id != original_root


@pytest.mark.asyncio
async def test_generating_blocks_picker(multi_tree_store):
    """Opening picker while generating should be a no-op."""
    store, ab, cd, ef = multi_tree_store
    session = LoomSession(store, ab[0].id)
    app = BasemodeApp(session)
    async with app.run_test(headless=True) as pilot:
        loom_screen = app.screen
        loom_screen._generating = True  # type: ignore[attr-defined]
        await pilot.press("tab")
        # Should still be on LoomScreen, not TreePickerScreen
        assert isinstance(app.screen, LoomScreen)
        loom_screen._generating = False  # type: ignore[attr-defined]
