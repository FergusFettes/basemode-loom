"""Tests for the keyword retrieval backend (synthetic FTS index)."""

from __future__ import annotations

from contextlib import closing

import pytest

from basemode_loom.retrieval import KeywordBackend, get_backend
from basemode_loom.retrieval.search import fts_match_query
from basemode_loom.store import GenerationStore


def _build_fts(store: GenerationStore, node_ids: list[str]) -> None:
    """Create + populate a corpus-shaped nodes_fts index for the given nodes."""
    with closing(store.connect()) as conn, conn:
        conn.execute(
            "CREATE VIRTUAL TABLE nodes_fts USING fts5("
            "node_id UNINDEXED, text, tokenize='unicode61')"
        )
        for node_id in node_ids:
            node = store.get(node_id)
            conn.execute(
                "INSERT INTO nodes_fts(node_id, text) VALUES (?, ?)",
                (node.id, node.text),
            )


@pytest.fixture
def store(tmp_path):
    return GenerationStore(tmp_path / "plain.sqlite")


@pytest.fixture
def fts_store(tmp_path):
    """Two trees with distinct vocabulary, indexed in a synthetic FTS table."""
    store = GenerationStore(tmp_path / "corpus.sqlite")
    _, cat = store.save_continuations(
        "the cat sat on the mat",
        [" a feline rested quietly"],
        model="m",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
    )
    _, dog = store.save_continuations(
        "dogs run fast in the park",
        [" canines sprint across grass"],
        model="m",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
    )
    all_nodes = store.tree(cat[0].id) + store.tree(dog[0].id)
    _build_fts(store, [n.id for n in all_nodes])
    return store, cat, dog


def test_fts_match_query_quotes_tokens():
    assert fts_match_query("cat dog") == '"cat" OR "dog"'
    assert fts_match_query("  !!  ") == ""


def test_status_reports_keyword_available(fts_store):
    store, _, _ = fts_store
    status = KeywordBackend(store).status()
    assert status.keyword is True
    assert status.semantic is False


def test_status_no_index(store):
    status = KeywordBackend(store).status()
    assert status.keyword is False
    assert status.semantic is False
    assert "no search index" in status.message


def test_search_matches_root_text(fts_store):
    store, cat, dog = fts_store
    hits = KeywordBackend(store).search("cat")
    assert [h.tree_id for h in hits] == [cat[0].tree_id]


def test_search_matches_child_text(fts_store):
    store, cat, dog = fts_store
    hits = KeywordBackend(store).search("feline")
    assert [h.tree_id for h in hits] == [cat[0].tree_id]
    assert store.get(hits[0].best_node_id).tree_id == cat[0].tree_id


def test_search_matches_node_id_without_index(store):
    _, children = store.save_continuations(
        "the ship rounded the headland",
        [" and sailed on"],
        model="m",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
    )
    child = children[0]
    hits = KeywordBackend(store).search(child.id)
    assert [h.tree_id for h in hits] == [child.tree_id]
    assert hits[0].best_node_id == child.id


def test_search_matches_node_id_prefix_without_index(store):
    _, children = store.save_continuations(
        "the ship rounded the headland",
        [" and sailed on"],
        model="m",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
    )
    child = children[0]
    hits = KeywordBackend(store).search(child.id[:8])
    assert [h.tree_id for h in hits] == [child.tree_id]


def test_search_rolls_up_multiple_node_hits_to_one_tree(fts_store):
    store, cat, dog = fts_store
    # Both "cat" (root) and "feline" (child) live in tree A; one TreeHit.
    hits = KeywordBackend(store).search("cat feline")
    tree_ids = [h.tree_id for h in hits]
    assert tree_ids.count(cat[0].tree_id) == 1


def test_search_separates_trees(fts_store):
    store, cat, dog = fts_store
    assert [h.tree_id for h in KeywordBackend(store).search("park")] == [dog[0].tree_id]


def test_empty_query_returns_nothing(fts_store):
    store, _, _ = fts_store
    assert KeywordBackend(store).search("") == []


def test_search_without_index_returns_nothing(store):
    store.save_continuations(
        "the ship rounded the headland",
        [" and sailed on"],
        model="m",
        strategy="s",
        max_tokens=10,
        temperature=0.9,
    )
    assert KeywordBackend(store).search("ship") == []


def test_semantic_results_are_rolled_up_to_trees(fts_store):
    store, cat, dog = fts_store
    backend = KeywordBackend(store)
    backend._semantic_status = lambda: (True, "")  # type: ignore[method-assign]
    backend._semantic_node_ids = lambda query, limit: [dog[0].id]  # type: ignore[method-assign]

    hits = backend.search("horticulture")

    assert [h.tree_id for h in hits] == [dog[0].tree_id]


def test_status_reports_vec_dependency_message(store):
    with closing(store.connect()) as conn, conn:
        conn.execute("CREATE TABLE nodes_vec(node_id TEXT PRIMARY KEY, embedding BLOB)")

    status = KeywordBackend(store).status()

    assert status.semantic is False
    assert "semantic index present" in status.message


def test_get_backend_returns_keyword_backend(store):
    assert isinstance(get_backend(store), KeywordBackend)
