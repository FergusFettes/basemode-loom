import pytest

from basemode_loom.session import (
    GenerationCancelled,
    GenerationComplete,
    GenerationError,
    LoomSession,
    TokenReceived,
)
from basemode_loom.store import GenerationStore


@pytest.fixture
def branched_store(tmp_path):
    """Root → [A, B] → A has child C."""
    store = GenerationStore(tmp_path / "test.sqlite")
    _, ab = store.save_continuations(
        "Root", ["A", "B"], model="m", strategy="system", max_tokens=10, temperature=0.9
    )
    _, c = store.save_continuations(
        "",
        ["C"],
        model="m",
        strategy="system",
        max_tokens=10,
        temperature=0.9,
        parent_id=ab[0].id,
    )
    return store, ab, c


# --- chat-shaped trees ---


def _chat_store(tmp_path):
    """Root (role=user) → child (role=assistant)."""
    store = GenerationStore(tmp_path / "chat.sqlite")
    root = store.create_root("hi", metadata={"role": "user"})
    child = store.add_child(
        root.id,
        "hello",
        model="m",
        strategy="system",
        max_tokens=10,
        temperature=0.9,
        metadata={"role": "assistant"},
    )
    return store, root, child


def test_chat_tree_enables_headers_and_segments(tmp_path):
    store, _root, child = _chat_store(tmp_path)
    state = LoomSession(store, child.id).get_state()
    assert state.render_chat_headers is True
    roles = [seg.role for seg in state.lineage_segments]
    assert roles == ["user", "assistant"]


def test_loom_tree_leaves_headers_off(branched_store):
    store, ab, _ = branched_store
    state = LoomSession(store, ab[0].id).get_state()
    assert state.render_chat_headers is False
    assert all(seg.role is None for seg in state.lineage_segments)


def test_toggle_chat_headers_suppresses_on_chat_tree(tmp_path):
    store, _root, child = _chat_store(tmp_path)
    session = LoomSession(store, child.id)
    assert session.get_state().render_chat_headers is True
    assert session.toggle_chat_headers().render_chat_headers is False
    # segments still carry roles even when rendering is suppressed
    assert session.get_state().lineage_segments[0].role == "user"


def test_toggle_chat_headers_noop_on_loom_tree(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    # toggling the user flag can't force headers onto a non-chat tree
    assert session.toggle_chat_headers().render_chat_headers is False


# --- get_state ---


def test_get_state_fields(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    state = session.get_state()
    assert state.current_node_id == ab[0].id
    assert state.model == session.model
    assert state.max_tokens == session.max_tokens
    assert state.n_branches == session.n_branches
    assert isinstance(state.full_text, str)


def test_get_state_children_listed(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    session.navigate_parent()
    state = session.get_state()
    assert len(state.children) == 2


def test_get_state_continuation_text(branched_store):
    store, ab, c = branched_store
    session = LoomSession(store, ab[0].id)
    session.navigate_parent()
    state = session.get_state()
    # Selected child (ab[0]) has a child C — continuation should include "C"
    assert "C" in state.continuation_text


# --- Navigation ---


def test_navigate_child_moves_into_child(branched_store):
    store, ab, c = branched_store
    session = LoomSession(store, ab[0].id)
    state = session.navigate_child()
    assert state.current_node_id == c[0].id


def test_navigate_child_at_leaf_is_noop(branched_store):
    store, ab, c = branched_store
    session = LoomSession(store, c[0].id)
    before = session._current_id
    state = session.navigate_child()
    assert state.current_node_id == before


def test_navigate_parent_moves_up(branched_store):
    store, ab, c = branched_store
    session = LoomSession(store, c[0].id)
    state = session.navigate_parent()
    assert state.current_node_id == ab[0].id


def test_navigate_parent_at_root_is_noop(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    session.navigate_parent()
    session.navigate_parent()  # already at root
    root_id = session._current_id
    state = session.navigate_parent()
    assert state.current_node_id == root_id


def test_navigate_parent_restores_sibling_index(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    session.navigate_parent()
    session.select_sibling(+1)  # select B
    session.navigate_child()  # go into B
    state = session.navigate_parent()
    assert state.selected_child_idx == 1  # should remember B was selected


def test_checkout_moves_directly_and_repairs_the_checked_out_lineage(branched_store):
    store, ab, c = branched_store
    session = LoomSession(store, c[0].id)

    state = session.checkout(c[0].id)

    assert state.current_node_id == ab[0].id
    assert state.children[state.selected_child_idx].id == c[0].id
    assert store.get_checked_out_child_id(ab[0].parent_id) == ab[0].id
    assert store.get_checked_out_child_id(ab[0].id) == c[0].id


def test_select_sibling_increments(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    session.navigate_parent()
    state = session.select_sibling(+1)
    assert state.selected_child_idx == 1


def test_select_sibling_wraps_after_last(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    session.navigate_parent()
    session.select_sibling(+1)
    state = session.select_sibling(+1)
    assert state.selected_child_idx == 0


def test_select_sibling_wraps_before_first(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    session.navigate_parent()
    state = session.select_sibling(-1)
    assert state.selected_child_idx == 1


def test_select_sibling_no_children_is_noop(branched_store):
    store, ab, c = branched_store
    session = LoomSession(store, c[0].id)
    state = session.select_sibling(+1)
    assert state.selected_child_idx == 0


def test_toggle_tree_view_adds_tree_nodes(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    state = session.toggle_tree_view()
    assert state.view_mode == "tree"
    assert state.tree_nodes is not None
    assert len(state.tree_nodes) == 4


def test_toggle_model_names_updates_state(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    state = session.toggle_model_names()
    assert state.show_model_names is False
    state = session.toggle_model_names()
    assert state.show_model_names is True


def test_toggle_hoist_uses_current_node(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    session.toggle_tree_view()
    state = session.toggle_hoist()
    assert state.hoisted_node_id == ab[0].id
    state = session.toggle_hoist()
    assert state.hoisted_node_id is None


def test_toggle_bookmark_persists_on_current_node(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    assert session.toggle_bookmark() is True
    node = store.get(ab[0].id)
    assert node is not None
    assert node.metadata["bookmarked"] is True
    assert session.toggle_bookmark() is False
    node = store.get(ab[0].id)
    assert node is not None
    assert node.metadata["bookmarked"] is False


def test_next_bookmark_moves_to_next_marked_node(branched_store):
    store, ab, _ = branched_store
    session = LoomSession(store, ab[0].id)
    store.update_metadata(ab[1].id, {"bookmarked": True})
    state = session.next_bookmark()
    assert state.current_node_id == ab[1].id


# --- Params ---


def test_set_max_tokens_clamps_high(store):
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.set_max_tokens(99999)
    assert session.max_tokens == 8000


def test_set_max_tokens_clamps_low(store):
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.set_max_tokens(0)
    assert session.max_tokens == 1


def test_set_n_branches_minimum_one(store):
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.set_n_branches(0)
    assert session.n_branches == 1
    session.set_n_branches(-5)
    assert session.n_branches == 1


def test_set_n_branches_applies_per_model_across_plan(store):
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.set_model_plan(
        [
            {"model": "m1", "n_branches": 1},
            {"model": "m2", "n_branches": 1},
            {"model": "m3", "n_branches": 1},
        ]
    )
    session.set_n_branches(2)
    assert session.branches_per_model == 2
    assert session.n_branches == 6
    assert [p.n_branches for p in session.model_plan] == [2, 2, 2]


# --- apply_edit ---


def test_apply_edit_single_node(store):
    _, ch = store.save_continuations(
        "Hello", [" world"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    original = store.full_text(session._current_id)
    assert original == "Hello world"
    new_node = session.apply_edit(original, "Hello earth")
    assert new_node is not None
    assert store.full_text(new_node.id) == "Hello earth"


def test_apply_edit_unchanged_returns_none(store):
    _, ch = store.save_continuations(
        "Hello", [" world"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    original = store.full_text(session._current_id)
    assert session.apply_edit(original, original) is None


def test_apply_edit_updates_current_id(store):
    _, ch = store.save_continuations(
        "Hello", [" world"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    original = store.full_text(session._current_id)
    old_id = session._current_id
    session.apply_edit(original, "Hello universe")
    assert session._current_id != old_id


def test_apply_edit_forks_at_changed_segment(store):
    _, ch1 = store.save_continuations(
        "Hello", [" world"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    _, ch2 = store.save_continuations(
        "",
        [" again"],
        model="m",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
        parent_id=ch1[0].id,
    )
    session = LoomSession(store, ch2[0].id)
    original = store.full_text(session._current_id)
    assert original == "Hello world again"
    # Edit the middle segment — should fork at ch1[0]
    new_node = session.apply_edit(original, "Hello earth again")
    assert new_node is not None
    assert store.full_text(new_node.id) == "Hello earth again"


def test_apply_edit_keeps_unchanged_nodes_below_the_edit(store):
    """A mid-lineage edit rewrites only the node it touched.

    A -> [B, C, D] becomes [A, A'] with A' owning B, C and D outright: the
    nodes below the edit keep their identity rather than being duplicated
    onto the new branch.
    """
    kwargs = dict(model="m", strategy="s", max_tokens=10, temperature=0.9)
    root = store.create_root("ROOT")
    a = store.add_child(root.id, " A\n", **kwargs)
    b = store.add_child(a.id, " B", **kwargs)
    c = store.add_child(a.id, " C", **kwargs)
    d = store.add_child(a.id, " D", **kwargs)
    e = store.add_child(b.id, " E", **kwargs)
    store.set_checked_out_child(root.id, a.id)
    store.set_checked_out_child(a.id, b.id)

    # Current node is B; the edit lands in A's segment, one node above it.
    session = LoomSession(store, b.id)
    current = session.apply_edit("ROOT A\n B", "ROOT A B")
    assert current is not None

    rewritten = [node for node in store.children(root.id) if node.id != a.id]
    assert len(rewritten) == 1
    new_a = rewritten[0]
    assert new_a.text == " A"

    # B, C and D moved wholesale -- no second copy of B anywhere.
    assert [node.id for node in store.children(new_a.id)] == [b.id, c.id, d.id]
    assert store.children(a.id) == []
    assert [node.id for node in store.children(b.id)] == [e.id]

    # The session stays on B, now reached through the rewritten A.
    assert current.id == b.id
    assert session._current_id == b.id
    assert store.full_text(b.id) == "ROOT A B"
    assert store.get_checked_out_child_id(root.id) == new_a.id
    assert store.get_checked_out_child_id(new_a.id) == b.id


def test_apply_edit_of_the_root_stays_in_the_same_tree(store):
    """Editing root text must not spawn a new tree and strand every branch.

    A root has no sibling position, so forking it meant calling create_root --
    which starts a fresh tree containing only a copy of the current path.
    """
    kwargs = dict(model="m", strategy="s", max_tokens=10, temperature=0.9)
    root = store.create_root("This is a small test tree")
    kept = store.add_child(root.id, " one", **kwargs)
    sibling = store.add_child(root.id, " two", **kwargs)
    leaf = store.add_child(kept.id, " leaf", **kwargs)
    store.set_checked_out_child(root.id, kept.id)
    store.set_checked_out_child(kept.id, leaf.id)

    session = LoomSession(store, leaf.id)
    current = session.apply_edit(
        "This is a small test tree one leaf", "This is a small test doc one leaf"
    )
    assert current is not None

    assert store.root(session._current_id).id == root.id
    assert store.get(root.id).text == "This is a small test doc"
    assert len(store.roots(archived=None)) == 1
    # Every branch is still hanging off the root, and nothing was duplicated.
    assert [node.id for node in store.children(root.id)] == [kept.id, sibling.id]
    assert [node.id for node in store.children(kept.id)] == [leaf.id]
    assert session._current_id == leaf.id
    assert store.full_text(leaf.id) == "This is a small test doc one leaf"


def test_apply_edit_checks_out_the_new_branch(store):
    _, ch1 = store.save_continuations(
        "Hello", [" world\n"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    root = store.root(ch1[0].id)
    store.set_checked_out_child(root.id, ch1[0].id)
    session = LoomSession(store, ch1[0].id)
    new_node = session.apply_edit(store.full_text(ch1[0].id), "Hello world")
    assert new_node is not None

    # The checked-out path has to follow the edit, not stay on the old text.
    assert store.get_checked_out_child_id(root.id) == new_node.id
    child_ids = [node.id for node in store.children(root.id)]
    assert session._child_path[root.id] == child_ids.index(new_node.id)
    # ...including for a session rebuilt from the store (a page reload).
    reloaded = LoomSession(store, root.id)
    assert reloaded._current_id == new_node.id
    assert reloaded.get_state().full_text == "Hello world"


def test_apply_edit_moves_children_onto_the_rewritten_node(store):
    _, ch1 = store.save_continuations(
        "Hello", [" world\n"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    _, ch2 = store.save_continuations(
        "",
        [" again", " once more"],
        model="m",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
        parent_id=ch1[0].id,
    )
    store.set_checked_out_child(ch1[0].id, ch2[1].id)
    session = LoomSession(store, ch1[0].id)
    new_node = session.apply_edit(store.full_text(ch1[0].id), "Hello world")
    assert new_node is not None

    moved = store.children(new_node.id)
    assert [node.id for node in moved] == [ch2[0].id, ch2[1].id]
    assert store.children(ch1[0].id) == []
    # The continuation that was checked out stays checked out after the move.
    assert store.get_checked_out_child_id(new_node.id) == ch2[1].id
    assert store.full_text(ch2[1].id) == "Hello world once more"


def test_edit_node_text_updates_selected_node_segment(store):
    _, ch = store.save_continuations(
        "Hello", [" world"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    edited = session.edit_node_text(ch[0].id, " earth")
    assert edited is not None
    state = session.get_state()
    assert state.current_node.text == " earth"
    assert state.full_text == "Hello earth"


def test_edit_node_text_does_not_use_full_text_diff_path(store, monkeypatch):
    _, ch = store.save_continuations(
        "Hello", [" world"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)

    def fail_apply(*_args, **_kwargs):
        raise AssertionError("should not call apply_edit for node edit")

    monkeypatch.setattr(session, "apply_edit", fail_apply)
    edited = session.edit_node_text(ch[0].id, " earth")
    assert edited is not None
    assert session.get_state().full_text == "Hello earth"


def test_delete_selected_child_removes_selected_subtree_and_keeps_parent(store):
    root = store.create_root("Root")
    left = store.add_child(
        root.id,
        " left",
        model="m",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
    )
    right = store.add_child(
        root.id,
        " right",
        model="m",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
    )
    leaf = store.add_child(
        left.id,
        " leaf",
        model="m",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
    )
    session = LoomSession(store, root.id)
    session.select_sibling(+1)  # select right
    assert session.delete_selected_child() is True
    state = session.get_state()
    assert state.current_node_id == root.id
    assert [c.id for c in state.children] == [left.id]
    assert state.selected_child_idx == 0
    assert store.get(right.id) is None
    assert store.get(left.id) is not None
    assert store.get(leaf.id) is not None


def test_delete_selected_child_no_children_is_false(store):
    root = store.create_root("Root")
    session = LoomSession(store, root.id)
    assert session.delete_selected_child() is False


# --- update_context ---


def test_update_context_persists(store):
    _, ch = store.save_continuations(
        "Text", ["more"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.update_context("You are a pirate.")
    current = store.get(ch[0].id)
    assert current is not None
    assert current.context_id is not None
    context = store.get(current.context_id)
    assert context is not None
    assert context.kind == "context"
    assert context.text == "You are a pirate."


def test_update_context_visible_in_state(store):
    _, ch = store.save_continuations(
        "Text", ["more"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.update_context("fantasy world")
    state = session.get_state()
    assert state.context == "fantasy world"


# --- save ---


def test_save_persists_model_and_tokens(store):
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.set_model("claude-3")
    session.set_max_tokens(400)
    session.set_n_branches(3)
    session.save()
    root = store.root(ch[0].id)
    tree = store.tree_for_node(root.id)
    assert tree.model_plan == [
        {
            "model": "claude-3",
            "max_tokens": 400,
            "n_branches": 3,
            "temperature": 0.9,
            "enabled": True,
            "pinned_settings": True,
        }
    ]
    assert tree.show_model_names is True
    assert "model" not in root.metadata
    assert "max_tokens" not in root.metadata
    assert "n_branches" not in root.metadata
    assert "show_model_names" not in root.metadata


def test_save_persists_model_name_visibility(store):
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.toggle_model_names()
    session.save()
    session2 = LoomSession(store, ch[0].id)
    assert session2.show_model_names is False


def test_save_restores_on_reload(store, tmp_path):
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.set_model("claude-3")
    session.set_max_tokens(400)
    session.save()
    session2 = LoomSession(store, ch[0].id)
    assert session2.model == "claude-3"
    assert session2.max_tokens == 400


# --- generate (with mocked continue_text) ---


@pytest.mark.asyncio
async def test_generate_yields_token_events(store, monkeypatch):
    async def fake_continue(prefix, model, **kwargs):
        for tok in ["hello", " world"]:
            yield tok

    monkeypatch.setattr("basemode_loom.session.continue_text", fake_continue)
    _, ch = store.save_continuations(
        "Prompt", ["start"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 1

    events = []
    async for event in session.generate():
        events.append(event)

    token_events = [e for e in events if isinstance(e, TokenReceived)]
    complete_events = [e for e in events if isinstance(e, GenerationComplete)]
    assert len(token_events) == 2
    assert token_events[0].token == "hello"
    assert len(complete_events) == 1


@pytest.mark.asyncio
async def test_generate_saves_completions(store, monkeypatch):
    async def fake_continue(prefix, model, **kwargs):
        yield "generated"

    monkeypatch.setattr("basemode_loom.session.continue_text", fake_continue)
    _, ch = store.save_continuations(
        "Prompt", ["seed"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 1

    async for event in session.generate():
        if isinstance(event, GenerationComplete):
            assert len(event.new_nodes) == 1

    # New node should exist in store
    new_children = store.children(ch[0].id)
    assert len(new_children) == 1
    assert "generated" in new_children[0].text


@pytest.mark.asyncio
async def test_generate_saves_normalized_completion_segments(store, monkeypatch):
    async def fake_continue(prefix, model, **kwargs):
        yield " itsu, Capoeira, and Fandango."

    monkeypatch.setattr("basemode_loom.session.continue_text", fake_continue)
    _, ch = store.save_continuations(
        "Prompt",
        ["some impossible marriage of Jiu-J"],
        model="m",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 1

    async for event in session.generate():
        if isinstance(event, GenerationComplete):
            assert event.new_nodes[0].text == "itsu, Capoeira, and Fandango."

    new_children = store.children(ch[0].id)
    assert new_children[0].text == "itsu, Capoeira, and Fandango."
    assert store.full_text(new_children[0].id).endswith(
        "Jiu-Jitsu, Capoeira, and Fandango."
    )


@pytest.mark.asyncio
async def test_generate_cancel(store, monkeypatch):
    import asyncio

    async def slow_continue(prefix, model, **kwargs):
        for i in range(100):
            await asyncio.sleep(0.01)
            yield f"t{i}"

    monkeypatch.setattr("basemode_loom.session.continue_text", slow_continue)
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 1

    events = []
    async for event in session.generate():
        events.append(event)
        if len(events) >= 3:
            session.cancel()

    assert any(isinstance(e, TokenReceived) for e in events)
    assert any(isinstance(e, GenerationCancelled) for e in events)
    assert not any(isinstance(e, GenerationComplete) for e in events)


@pytest.mark.asyncio
async def test_generate_error_propagated(store, monkeypatch):
    async def failing_continue(prefix, model, **kwargs):
        yield "before"
        raise RuntimeError("API down")

    monkeypatch.setattr("basemode_loom.session.continue_text", failing_continue)
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 1

    events = []
    async for event in session.generate():
        events.append(event)

    error_events = [e for e in events if isinstance(e, GenerationError)]
    assert len(error_events) == 1
    assert "API down" not in str(error_events[0].error)
    assert error_events[0].category == "provider_error"
    assert error_events[0].incident_id is not None
    assert not any(isinstance(e, GenerationComplete) for e in events)


@pytest.mark.asyncio
async def test_generate_empty_stream_is_reported_as_error_not_saved(store, monkeypatch):
    async def empty_continue(prefix, model, **kwargs):
        return
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr("basemode_loom.session.continue_text", empty_continue)
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 1

    events = []
    async for event in session.generate():
        events.append(event)

    assert not any(isinstance(e, GenerationComplete) for e in events)
    error_events = [e for e in events if isinstance(e, GenerationError)]
    assert len(error_events) == 1
    assert error_events[0].category == "empty_response"
    assert error_events[0].failures[0].model == session.model
    assert error_events[0].failures[0].branch_idx == 0
    assert store.children(ch[0].id) == []


@pytest.mark.asyncio
async def test_generate_whitespace_only_stream_is_reported_as_error_not_saved(
    store, monkeypatch
):
    async def whitespace_continue(prefix, model, **kwargs):
        yield "   "
        yield "\n"

    monkeypatch.setattr("basemode_loom.session.continue_text", whitespace_continue)
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 1

    events = []
    async for event in session.generate():
        events.append(event)

    assert not any(isinstance(e, GenerationComplete) for e in events)
    error_events = [e for e in events if isinstance(e, GenerationError)]
    assert len(error_events) == 1
    assert error_events[0].category == "empty_response"
    assert store.children(ch[0].id) == []


@pytest.mark.asyncio
async def test_generate_empty_completion_error_reports_finish_reason(
    store, monkeypatch
):
    from basemode.exceptions import EmptyCompletionError

    async def raising_continue(prefix, model, **kwargs):
        raise EmptyCompletionError(
            model=model, strategy="system", finish_reason="content_filter"
        )
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr("basemode_loom.session.continue_text", raising_continue)
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 1

    events = []
    async for event in session.generate():
        events.append(event)

    error_events = [e for e in events if isinstance(e, GenerationError)]
    assert len(error_events) == 1
    assert error_events[0].category == "empty_response"
    assert error_events[0].finish_reason == "content_filter"


@pytest.mark.asyncio
async def test_generate_empty_branch_alongside_successful_branch(store, monkeypatch):
    calls = 0

    async def mixed_continue(prefix, model, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield "ok"
            return
        return
        yield  # pragma: no cover

    monkeypatch.setattr("basemode_loom.session.continue_text", mixed_continue)
    monkeypatch.setattr(
        "basemode_loom.session.normalize_completion_segment",
        lambda _prefix, completion: completion,
    )
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 2

    events = []
    async for event in session.generate():
        events.append(event)

    complete_events = [e for e in events if isinstance(e, GenerationComplete)]
    error_events = [e for e in events if isinstance(e, GenerationError)]
    assert len(complete_events) == 1
    assert len(complete_events[0].new_nodes) == 1
    assert complete_events[0].new_nodes[0].text == "ok"
    assert len(error_events) == 1
    assert error_events[0].category == "empty_response"

    new_children = store.children(ch[0].id)
    assert len(new_children) == 1
    assert new_children[0].text == "ok"


@pytest.mark.asyncio
async def test_generate_partial_failure_still_saves_successful_branches(
    store, monkeypatch
):
    calls = 0

    async def mixed_continue(prefix, model, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield "ok"
            return
        raise RuntimeError("branch failed")

    monkeypatch.setattr("basemode_loom.session.continue_text", mixed_continue)
    monkeypatch.setattr(
        "basemode_loom.session.normalize_completion_segment",
        lambda _prefix, completion: completion,
    )
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 2

    events = []
    async for event in session.generate():
        events.append(event)

    complete_events = [e for e in events if isinstance(e, GenerationComplete)]
    error_events = [e for e in events if isinstance(e, GenerationError)]
    assert len(complete_events) == 1
    assert len(complete_events[0].new_nodes) == 1
    assert complete_events[0].new_nodes[0].text == "ok"
    assert len(error_events) == 1
    assert "branch failed" in str(error_events[0].error)

    new_children = store.children(ch[0].id)
    assert len(new_children) == 1
    assert new_children[0].text == "ok"


@pytest.mark.asyncio
async def test_generate_saves_to_source_node_even_if_current_moves(store, monkeypatch):
    import asyncio

    gate = asyncio.Event()

    async def gated_continue(prefix, model, **kwargs):
        await gate.wait()
        yield "done"

    monkeypatch.setattr("basemode_loom.session.continue_text", gated_continue)
    _, ch = store.save_continuations(
        "Prompt", ["seed"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    source_id = session.get_state().current_node_id

    async def run():
        events = []
        async for event in session.generate():
            events.append(event)
        return events

    task = asyncio.create_task(run())
    await asyncio.sleep(0)
    session.navigate_parent()
    gate.set()
    events = await task

    assert any(isinstance(e, GenerationComplete) for e in events)
    source_children = store.children(source_id)
    assert any(c.text == "done" for c in source_children)


@pytest.mark.asyncio
async def test_generate_multiple_branches(store, monkeypatch):
    call_count = 0

    async def fake_continue(prefix, model, **kwargs):
        nonlocal call_count
        call_count += 1
        yield f"branch{call_count}"

    monkeypatch.setattr("basemode_loom.session.continue_text", fake_continue)
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 3

    async for event in session.generate():
        if isinstance(event, GenerationComplete):
            assert len(event.new_nodes) == 3
            assert call_count == 3


@pytest.mark.asyncio
async def test_generate_uses_globals_only_for_pinned_models(store, monkeypatch):
    calls: list[tuple[str, int]] = []

    async def fake_continue(prefix, model, **kwargs):
        calls.append((model, kwargs["max_tokens"]))
        yield "generated"

    monkeypatch.setattr("basemode_loom.session.continue_text", fake_continue)
    root = store.create_root("Prompt")
    session = LoomSession(store, root.id)
    session.set_model_plan(
        [
            {
                "model": "pinned-model",
                "n_branches": 1,
                "max_tokens": 20,
                "temperature": 0.9,
                "enabled": True,
                "pinned_settings": True,
            },
            {
                "model": "private-model",
                "n_branches": 3,
                "max_tokens": 30,
                "temperature": 0.9,
                "enabled": True,
                "pinned_settings": False,
            },
        ]
    )
    session.global_n_branches = 2
    session.global_max_tokens = 200

    async for _event in session.generate():
        pass

    assert calls.count(("pinned-model", 200)) == 2
    assert calls.count(("private-model", 30)) == 3
    assert session.model_plan[0].max_tokens == 20


@pytest.mark.asyncio
async def test_generate_shuffles_completion_order(store, monkeypatch):
    async def fake_continue(prefix, model, **kwargs):
        yield "x"

    def reverse(items):
        items.reverse()

    monkeypatch.setattr("basemode_loom.session.continue_text", fake_continue)
    monkeypatch.setattr("basemode_loom.session.random.shuffle", reverse)
    monkeypatch.setattr(
        "basemode_loom.session.normalize_completion_segment",
        lambda _prefix, completion: completion,
    )
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 3

    async for _event in session.generate():
        pass

    new_children = store.children(ch[0].id)
    assert [c.metadata["model_branch_index"] for c in new_children] == [2, 1, 0]


@pytest.mark.asyncio
async def test_generate_accepts_forced_openrouter_model(store, monkeypatch):
    async def fake_continue(prefix, model, **kwargs):
        assert model == "openrouter/moonshotai/kimi-k2.6"
        yield "generated"

    class _Strategy:
        name = "system"

    monkeypatch.setattr("basemode_loom.session.continue_text", fake_continue)
    monkeypatch.setattr(
        "basemode_loom.session.detect_strategy", lambda model, _: _Strategy()
    )
    _, ch = store.save_continuations(
        "Prompt", ["seed"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.set_model("or:moonshotai/kimi-k2.6")
    session.n_branches = 1

    async for event in session.generate():
        if isinstance(event, GenerationComplete):
            assert event.new_nodes[0].model == "openrouter/moonshotai/kimi-k2.6"


@pytest.mark.asyncio
async def test_generate_persists_usage_metadata_and_tree_cost(store, monkeypatch):
    async def fake_continue(prefix, model, **kwargs):
        yield "generated"

    class _Strategy:
        name = "system"

    class _Usage:
        model = "gpt-4o-mini"
        prompt_tokens = 12
        completion_tokens = 7
        total_tokens = 19
        cost_usd = 0.00123
        pricing_available = True

    monkeypatch.setattr("basemode_loom.session.continue_text", fake_continue)
    monkeypatch.setattr(
        "basemode_loom.session.detect_strategy", lambda model, _: _Strategy()
    )
    monkeypatch.setattr(
        "basemode_loom.session.estimate_usage", lambda *a, **k: _Usage()
    )
    _, ch = store.save_continuations(
        "Prompt", ["seed"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.n_branches = 1

    async for _event in session.generate():
        pass

    children = store.children(ch[0].id)
    assert len(children) == 1
    usage = children[0].metadata["usage"]
    assert usage["prompt_tokens"] == 12
    assert usage["completion_tokens"] == 7
    assert usage["total_tokens"] == 19
    assert usage["cost_usd"] == pytest.approx(0.00123)
    assert usage["pricing_available"] is True

    state = session.get_state()
    assert state.tree_total_tokens == 19
    assert state.tree_cost_usd == pytest.approx(0.00123)
    assert state.tree_pricing_complete is True


def test_default_model_plan_is_pinned_to_global_settings(store):
    """A tree with no configured plan follows the global branches/tokens."""
    root, ch = store.save_continuations(
        "Hello", [" world"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    assert session.model_plan[0].pinned_settings is True


def test_setters_write_through_a_pinned_entry_to_the_globals(store):
    """A pinned entry generates with the globals, so the setters must move them.

    Otherwise the TUI and CLI, which set branches and tokens on the plan entry,
    would silently generate with the global values instead.
    """
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    assert session.model_plan[0].pinned_settings is True

    session.set_n_branches(3)
    session.set_max_tokens(400)
    assert session.global_n_branches == 3
    assert session.global_max_tokens == 400


def test_setters_leave_the_globals_alone_for_an_unpinned_entry(store):
    _, ch = store.save_continuations(
        "X", ["Y"], model="m", strategy="s", max_tokens=10, temperature=0.9
    )
    session = LoomSession(store, ch[0].id)
    session.set_model_plan(
        [{"model": "m", "n_branches": 1, "max_tokens": 200, "temperature": 0.9}]
    )
    assert session.model_plan[0].pinned_settings is False

    session.set_n_branches(3)
    session.set_max_tokens(400)
    assert session.global_n_branches == 1
    assert session.global_max_tokens == 200
