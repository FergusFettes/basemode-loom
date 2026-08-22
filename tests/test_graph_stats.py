from __future__ import annotations

import pytest

from basemode_loom.graph_stats import analyze_subtree
from basemode_loom.store import GenerationStore


def _child(store, parent, suffix, **kw):
    return store.add_child(
        parent.id,
        suffix,
        model=kw.get("model", "gpt-4o-mini"),
        strategy="system",
        max_tokens=5,
        temperature=0.7,
    )


def test_analyze_subtree_of_single_leaf(tmp_path) -> None:
    store = GenerationStore(tmp_path / "g.sqlite")
    root = store.create_root("root")

    shape = analyze_subtree(store, root.id)

    assert shape.subtree_size == 1
    assert shape.descendant_count == 0
    assert shape.child_count == 0
    assert shape.leaf_count == 1
    assert shape.internal_count == 0
    assert shape.max_depth == 0
    assert shape.max_width == 1
    assert shape.avg_branching_factor == 0.0
    assert shape.branchiness == 0.0


def test_analyze_subtree_pure_chain_has_zero_branchiness(tmp_path) -> None:
    store = GenerationStore(tmp_path / "g.sqlite")
    root = store.create_root("root")
    a = _child(store, root, " a")
    b = _child(store, a, " b")
    _child(store, b, " c")

    shape = analyze_subtree(store, root.id)

    assert shape.subtree_size == 4
    assert shape.descendant_count == 3
    assert shape.child_count == 1
    assert shape.leaf_count == 1
    assert shape.max_depth == 3
    assert shape.max_width == 1
    assert shape.avg_branching_factor == 1.0
    assert shape.branchiness == 0.0


def test_analyze_subtree_star_has_max_branchiness(tmp_path) -> None:
    store = GenerationStore(tmp_path / "g.sqlite")
    root = store.create_root("root")
    _child(store, root, " a")
    _child(store, root, " b")
    _child(store, root, " c")

    shape = analyze_subtree(store, root.id)

    assert shape.subtree_size == 4
    assert shape.descendant_count == 3
    assert shape.child_count == 3
    assert shape.leaf_count == 3
    assert shape.max_depth == 1
    assert shape.max_width == 3
    assert shape.avg_branching_factor == 3.0
    assert shape.branchiness == 1.0


def test_analyze_subtree_scoped_to_given_node_not_whole_tree(tmp_path) -> None:
    store = GenerationStore(tmp_path / "g.sqlite")
    root = store.create_root("root")
    a = _child(store, root, " a")
    _child(store, a, " a1")
    _child(store, a, " a2")
    _child(store, root, " b")  # sibling of `a`, outside the subtree rooted at `a`

    shape = analyze_subtree(store, a.id)

    assert shape.node_id == a.id
    assert shape.subtree_size == 3
    assert shape.descendant_count == 2
    assert shape.child_count == 2


def test_analyze_subtree_unknown_node_raises(tmp_path) -> None:
    store = GenerationStore(tmp_path / "g.sqlite")

    with pytest.raises(KeyError):
        analyze_subtree(store, "missing")
