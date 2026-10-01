"""Tests for building the FTS5 keyword index from a loom database."""

from __future__ import annotations

from contextlib import closing

import pytest
from typer.testing import CliRunner

from basemode_loom import chat
from basemode_loom.cli import app
from basemode_loom.retrieval import KeywordBackend
from basemode_loom.retrieval.keyword import build_fts_index

runner = CliRunner()


def _two_trees(store):
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
    return cat[0], dog[0]


def test_full_build_makes_keyword_search_work(store) -> None:
    cat, _dog = _two_trees(store)

    added = build_fts_index(store.db_path)

    assert added == 4
    assert KeywordBackend(store).status().keyword is True
    hits = KeywordBackend(store).search("feline")
    assert [hit.tree_id for hit in hits] == [cat.tree_id]


def test_incremental_adds_new_nodes_and_prunes_deleted(store) -> None:
    cat, dog = _two_trees(store)
    build_fts_index(store.db_path)
    store.add_child(
        cat.id, " then purred", model="m", strategy="s", max_tokens=5, temperature=1
    )
    store.delete_tree(dog.tree_id)

    added = build_fts_index(store.db_path, incremental=True)

    assert added == 1
    assert KeywordBackend(store).search("purred")[0].tree_id == cat.tree_id
    assert KeywordBackend(store).search("canines") == []


def test_min_chars_skips_short_nodes(store) -> None:
    _two_trees(store)
    store.create_root("hi")

    assert build_fts_index(store.db_path, min_chars=5) == 4


def test_refuses_to_replace_a_foreign_index(store) -> None:
    with closing(store.connect()) as conn, conn:
        conn.execute("CREATE VIRTUAL TABLE nodes_fts USING fts5(node_id, body, extra)")

    with pytest.raises(ValueError, match="refusing"):
        build_fts_index(store.db_path)


def test_index_command_covers_chat_databases(tmp_path) -> None:
    from basemode_loom.store import GenerationStore

    db = tmp_path / "chats.sqlite"
    store = GenerationStore(db)
    root = chat.start_chat(store, "tell me about puffins on Skomer")

    result = runner.invoke(app, ["index", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert "Indexed 1" in result.output
    assert KeywordBackend(store).search("puffins")[0].tree_id == root.tree_id
