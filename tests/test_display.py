from basemode_loom.display import (
    build_loom_display,
    build_stream_display,
    build_tree_display,
    word_wrap_inline,
    wrap_text,
)
from basemode_loom.session import LineageSegment, SessionState
from basemode_loom.store import Node


def _node(id, parent_id=None, root_id=None, text="", model=None, role=None):
    return Node(
        id=id,
        parent_id=parent_id,
        tree_id=root_id or id,
        text=text,
        model=model,
        strategy=None,
        max_tokens=None,
        temperature=None,
        created_at="2024-01-01T00:00:00Z",
        metadata={"role": role} if role else {},
    )


def _state(
    full_text="Hello",
    children=None,
    selected_idx=0,
    continuation_text="",
    descendant_counts=None,
):
    node = _node("root", text=full_text)
    return SessionState(
        current_node_id="root",
        current_node=node,
        full_text=full_text,
        children=children or [],
        selected_child_idx=selected_idx,
        descendant_counts=descendant_counts or {},
        continuation_text=continuation_text,
        model="gpt-4o-mini",
        max_tokens=200,
        temperature=0.9,
        n_branches=1,
        context="",
        root_id="root",
    )


# --- wrap_text ---


def test_wrap_text_single_line():
    assert wrap_text("hello", 80) == ["hello"]


def test_wrap_text_wraps_long_line():
    lines = wrap_text("one two three", 7)
    assert len(lines) > 1
    assert all(len(l) <= 7 for l in lines)


def test_wrap_text_preserves_blank_lines():
    lines = wrap_text("a\n\nb", 80)
    assert lines == ["a", "", "b"]


def test_wrap_text_leading_blank():
    lines = wrap_text("\nfoo", 80)
    assert lines[0] == ""
    assert "foo" in lines


def test_wrap_text_empty_returns_one_empty():
    assert wrap_text("", 80) == [""]


def test_wrap_text_only_newline():
    lines = wrap_text("\n", 80)
    assert "" in lines


# --- word_wrap_inline ---


def test_word_wrap_inline_short_fits_on_first_line():
    assert word_wrap_inline("hi", 20, 80) == ["hi"]


def test_word_wrap_inline_uses_first_width_then_full():
    # "one two" with first_width=4 should split: "one" on first, "two" on second (full_width=20)
    lines = word_wrap_inline("one two", 4, 20)
    assert lines[0] == "one"
    assert lines[1] == "two"


def test_word_wrap_inline_empty_returns_one_empty():
    assert word_wrap_inline("", 20, 80) == [""]


def test_word_wrap_inline_long_word_truncates_to_first_width():
    lines = word_wrap_inline("toolongword rest", 5, 20)
    assert len(lines[0]) <= 5


def test_word_wrap_inline_subsequent_lines_use_full_width():
    lines = word_wrap_inline("a b c d e f g h i j", 3, 80)
    # Second line onwards should not be constrained to first_width
    if len(lines) > 1:
        assert len(lines[1]) <= 80


# --- build_loom_display ---


def test_build_loom_display_no_children_all_normal():
    state = _state(full_text="Hello world", children=[])
    lines = build_loom_display(state, 80)
    assert lines
    assert all(l.style == "normal" for l in lines)
    text = "".join(l.text for l in lines)
    assert "Hello world" in text


def test_build_loom_display_selected_child_is_bold():
    child = _node("c1", parent_id="root", text=" continuation")
    state = _state(full_text="Hello", children=[child], selected_idx=0)
    lines = build_loom_display(state, 80)
    bold = [l for l in lines if l.style == "bold"]
    assert len(bold) > 0
    assert any("continuation" in l.text for l in bold)


def test_build_loom_display_other_siblings_are_dim():
    c1 = _node("c1", parent_id="root", text=" one")
    c2 = _node("c2", parent_id="root", text=" two")
    state = _state(full_text="Hello", children=[c1, c2], selected_idx=0)
    lines = build_loom_display(state, 80)
    dim = [l for l in lines if l.style == "dim"]
    assert len(dim) > 0
    assert any("two" in l.text for l in dim)
    assert not any("one" in l.text for l in dim)


def test_build_loom_display_arrow_present():
    child = _node("c1", parent_id="root", text=" next")
    state = _state(full_text="Start", children=[child])
    lines = build_loom_display(state, 80)
    assert any(" -> " in l.text for l in lines)


def test_build_loom_display_continuation_appended_as_normal():
    child = _node("c1", parent_id="root", text=" A")
    state = _state(
        full_text="Hello", children=[child], continuation_text=" deeper text"
    )
    lines = build_loom_display(state, 80)
    normal_with_deeper = [
        l for l in lines if l.style == "normal" and "deeper" in l.text
    ]
    assert len(normal_with_deeper) > 0


def test_build_loom_display_descendant_count_in_marker():
    child = _node("c1", parent_id="root", text=" A")
    state = _state(
        full_text="X",
        children=[child],
        selected_idx=0,
        descendant_counts={"c1": 5},
    )
    lines = build_loom_display(state, 80)
    text = "".join(l.text for l in lines)
    assert "(5)" in text


def test_build_loom_display_wraps_to_width():
    long_text = "word " * 30
    child = _node("c1", parent_id="root", text=long_text)
    state = _state(full_text="Hello", children=[child])
    lines = build_loom_display(state, 40)
    assert all(len(l.text) <= 45 for l in lines)  # some tolerance for indent+arrow


def test_build_loom_display_narrow_terminal_falls_back():
    # When terminal is very narrow, last_line should be pushed to its own line
    child = _node("c1", parent_id="root", text=" x")
    very_long_parent = "A" * 75
    state = _state(full_text=very_long_parent, children=[child])
    lines = build_loom_display(state, 80)
    assert lines  # should not crash


# --- build_loom_display: chat role headers ---


def _segs(pairs):
    return tuple(
        LineageSegment(text=text, role=role, node_id=f"k{i}")
        for i, (role, text) in enumerate(pairs)
    )


def _chat_state(
    segments,
    children=None,
    selected_idx=0,
    render_chat_headers=True,
    continuation=(),
):
    """Build a SessionState whose path is described by (role, text) segments.

    The last segment is treated as the current node (as in real lineages).
    """
    lineage = tuple(
        LineageSegment(text=text, role=role, node_id=f"n{i}")
        for i, (role, text) in enumerate(segments)
    )
    full_text = "".join(text for _, text in segments)
    current_node_id = f"n{len(segments) - 1}"
    current_role = segments[-1][0]
    cur = _node(current_node_id, text=segments[-1][1], role=current_role)
    return SessionState(
        current_node_id=current_node_id,
        current_node=cur,
        full_text=full_text,
        children=children or [],
        selected_child_idx=selected_idx,
        descendant_counts={},
        continuation_text="".join(t for _, t in continuation),
        model="gpt-4o-mini",
        max_tokens=200,
        temperature=0.9,
        n_branches=1,
        context="",
        root_id="n0",
        render_chat_headers=render_chat_headers,
        lineage_segments=lineage,
        continuation_segments=_segs(continuation),
    )


def test_chat_headers_off_renders_flat():
    state = _chat_state(
        [("user", "hi"), ("assistant", "hello")],
        render_chat_headers=False,
    )
    lines = build_loom_display(state, 80)
    assert not any(line.style == "header" for line in lines)
    assert "".join(line.text for line in lines) == "hihello"


def test_chat_headers_inserted_on_role_change():
    state = _chat_state([("user", "hi"), ("assistant", "hello")])
    lines = build_loom_display(state, 80)
    headers = [line for line in lines if line.style == "header"]
    assert [h.text.split()[1] for h in headers] == ["USER", "ASSISTANT"]
    # header sits before its turn's text
    texts = [line.text for line in lines]
    assert texts.index("hi") > 0
    assert any("hello" in t for t in texts)


def test_chat_split_turn_shares_one_header():
    # one assistant turn stored across two continuation nodes -> single header
    state = _chat_state(
        [("user", "hi"), ("assistant", "hel"), ("assistant", "lo")],
    )
    lines = build_loom_display(state, 80)
    assistant_headers = [
        line for line in lines if line.style == "header" and "ASSISTANT" in line.text
    ]
    assert len(assistant_headers) == 1


def test_loom_tree_never_gets_headers():
    # roles all None -> no headers even with the flag on
    state = _chat_state([(None, "once "), (None, "upon "), (None, "a time")])
    lines = build_loom_display(state, 80)
    assert not any(line.style == "header" for line in lines)


def test_chat_child_starting_new_turn_gets_header():
    child = _node("c1", parent_id="cur", text="Paris.", role="assistant")
    state = _chat_state(
        [("user", "capital of France?")],
        children=[child],
    )
    lines = build_loom_display(state, 80)
    header_labels = [line.text.split()[1] for line in lines if line.style == "header"]
    assert "ASSISTANT" in header_labels
    bold = [line for line in lines if line.style == "bold"]
    assert any("Paris" in line.text for line in bold)
    # new turn should not be inlined onto the user's line via an arrow
    assert not any(" -> " in line.text and "capital" in line.text for line in lines)


def test_chat_same_role_child_stays_inline():
    # assistant continuing its own turn -> keep the inline arrow, no new header
    child = _node("c1", parent_id="cur", text=" more", role="assistant")
    state = _chat_state(
        [("user", "q"), ("assistant", "ans")],
        children=[child],
    )
    lines = build_loom_display(state, 80)
    assert any(" -> " in line.text for line in lines)
    assistant_headers = [
        line for line in lines if line.style == "header" and "ASSISTANT" in line.text
    ]
    assert len(assistant_headers) == 1  # only the path header, none added for child


def test_chat_continuation_path_gets_headers():
    # the checked-out path below the selected child must also break by role
    child = _node("c1", parent_id="n0", text="answer one", role="assistant")
    state = _chat_state(
        [("user", "first question")],
        children=[child],
        continuation=[("user", "second question"), ("assistant", "answer two")],
    )
    lines = build_loom_display(state, 80)
    labels = [line.text.split()[1] for line in lines if line.style == "header"]
    # USER (prefix) -> ASSISTANT (child) -> USER -> ASSISTANT (continuation)
    assert labels == ["USER", "ASSISTANT", "USER", "ASSISTANT"]


def test_chat_continuation_same_role_no_redundant_header():
    # selected child is assistant; continuation keeps speaking as assistant
    child = _node("c1", parent_id="n0", text="part one ", role="assistant")
    state = _chat_state(
        [("user", "q")],
        children=[child],
        continuation=[("assistant", "part two")],
    )
    lines = build_loom_display(state, 80)
    labels = [line.text.split()[1] for line in lines if line.style == "header"]
    assert labels == ["USER", "ASSISTANT"]  # no second ASSISTANT at the boundary


# --- build_tree_display ---


def test_build_tree_display_shows_full_tree():
    root = _node("root", text="Root")
    c1 = _node("c1", parent_id="root", root_id="root", text=" first")
    c2 = _node("c2", parent_id="root", root_id="root", text=" second")
    state = _state(full_text="Root", children=[c1], selected_idx=0)
    state = SessionState(
        **{**state.__dict__, "view_mode": "tree", "tree_nodes": [root, c1, c2]}
    )
    lines = build_tree_display(state, 80)
    text = "\n".join(line.text for line in lines)
    assert "first" in text
    assert "second" in text


def test_build_tree_display_marks_current_and_bookmark():
    root = _node("root", text="Root")
    c1 = _node("c1", parent_id="root", root_id="root", text=" first")
    c2 = _node(
        "c2",
        parent_id="root",
        root_id="root",
        text=" second",
        model="openai/gpt-4o-mini",
    )
    c2 = Node(**{**c2.__dict__, "metadata": {"bookmarked": True}})
    state = SessionState(
        current_node_id="c2",
        current_node=c2,
        full_text="Root second",
        children=[],
        selected_child_idx=0,
        descendant_counts={},
        continuation_text="",
        model="gpt-4o-mini",
        max_tokens=200,
        temperature=0.9,
        n_branches=1,
        context="",
        root_id="root",
        view_mode="tree",
        tree_nodes=[root, c1, c2],
    )
    lines = build_tree_display(state, 80)
    text = "\n".join(line.text for line in lines)
    assert ">b gpt-4o-mini" in text
    assert "c2" not in text
    current = next(line for line in lines if line.style == "current")
    assert current.spans[0].style == "model"
    assert "second" in current.text


def test_build_tree_display_marks_checked_out_path():
    root = _node("root", text="Root")
    c1 = _node("c1", parent_id="root", root_id="root", text=" first")
    gc = _node("gc", parent_id="c1", root_id="root", text=" deeper")
    state = SessionState(
        current_node_id="gc",
        current_node=gc,
        full_text="Root first deeper",
        children=[],
        selected_child_idx=0,
        descendant_counts={},
        continuation_text="",
        model="gpt-4o-mini",
        max_tokens=200,
        temperature=0.9,
        n_branches=1,
        context="",
        root_id="root",
        view_mode="tree",
        tree_nodes=[root, c1, gc],
    )
    lines = build_tree_display(state, 80)
    assert any(line.style == "path" and "Root" in line.text for line in lines)
    assert any(line.style == "path" and "first" in line.text for line in lines)
    assert any(line.style == "current" and "deeper" in line.text for line in lines)


def test_build_tree_display_marks_selected_child():
    root = _node("root", text="Root")
    c1 = _node("c1", parent_id="root", root_id="root", text=" first")
    c2 = _node(
        "c2", parent_id="root", root_id="root", text=" second", model="anthropic/claude"
    )
    state = SessionState(
        current_node_id="root",
        current_node=root,
        full_text="Root",
        children=[c1, c2],
        selected_child_idx=1,
        descendant_counts={},
        continuation_text="",
        model="gpt-4o-mini",
        max_tokens=200,
        temperature=0.9,
        n_branches=1,
        context="",
        root_id="root",
        view_mode="tree",
        tree_nodes=[root, c1, c2],
    )
    lines = build_tree_display(state, 80)
    assert any(line.style == "selected" and "*  claude" in line.text for line in lines)


def test_build_tree_display_can_hide_model_names():
    root = _node("root", text="Root")
    child = _node(
        "child",
        parent_id="root",
        root_id="root",
        text=" child text",
        model="openai/gpt-4o-mini",
    )
    state = SessionState(
        current_node_id="root",
        current_node=root,
        full_text="Root",
        children=[child],
        selected_child_idx=0,
        descendant_counts={},
        continuation_text="",
        model="gpt-4o-mini",
        max_tokens=200,
        temperature=0.9,
        n_branches=1,
        context="",
        root_id="root",
        view_mode="tree",
        tree_nodes=[root, child],
        show_model_names=False,
    )
    text = "\n".join(line.text for line in build_tree_display(state, 80))
    assert "gpt-4o-mini" not in text
    assert "child text" in text


def test_build_tree_display_hoists_subtree():
    root = _node("root", text="Root")
    c1 = _node("c1", parent_id="root", root_id="root", text=" first")
    c2 = _node("c2", parent_id="root", root_id="root", text=" second")
    gc = _node("gc", parent_id="c1", root_id="root", text=" deeper")
    state = SessionState(
        current_node_id="gc",
        current_node=gc,
        full_text="Root first deeper",
        children=[],
        selected_child_idx=0,
        descendant_counts={},
        continuation_text="",
        model="gpt-4o-mini",
        max_tokens=200,
        temperature=0.9,
        n_branches=1,
        context="",
        root_id="root",
        view_mode="tree",
        hoisted_node_id="c1",
        tree_nodes=[root, c1, c2, gc],
    )
    lines = build_tree_display(state, 80)
    text = "\n".join(line.text for line in lines)
    assert "[hoist]" in text
    assert "c1" not in text
    assert "deeper" in text
    assert "second" not in text


# --- build_stream_display ---


def test_build_stream_display_single_branch_bold():
    buffers = [["hello", " world"]]
    lines = build_stream_display("Prefix", buffers, 80)
    bold = [l for l in lines if l.style == "bold"]
    assert any("hello" in l.text for l in bold)


def test_build_stream_display_second_branch_dim():
    buffers = [["first"], ["second"]]
    lines = build_stream_display("Prefix", buffers, 80)
    dim = [l for l in lines if l.style == "dim"]
    assert any("second" in l.text for l in dim)


def test_build_stream_display_arrow_present():
    buffers = [["x"]]
    lines = build_stream_display("prefix text", buffers, 80)
    assert any(" -> " in l.text for l in lines)


def test_build_stream_display_empty_buffers_no_crash():
    lines = build_stream_display("", [], 80)
    assert isinstance(lines, list)


def test_build_stream_display_parent_lines_present():
    buffers = [["cont"]]
    lines = build_stream_display("Parent text", buffers, 80)
    all_text = "".join(l.text for l in lines)
    assert "Parent" in all_text


def test_build_stream_display_cursor_glyph_in_active():
    buffers = [["token"]]
    lines = build_stream_display("X", buffers, 80)
    all_text = "".join(l.text for l in lines if l.style == "bold")
    assert "▋" in all_text
