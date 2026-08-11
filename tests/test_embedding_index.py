from __future__ import annotations

from contextlib import closing
from pathlib import Path

from typer.testing import CliRunner

from basemode_loom.cli import app
from basemode_loom.retrieval import KeywordBackend
from basemode_loom.retrieval.embedder import get_embedder
from basemode_loom.retrieval.vectors import (
    embed_corpus,
    load_vec,
    read_meta,
    vector_count,
    vector_search,
)
from basemode_loom.store import GenerationStore


class StubEmbedder:
    name = "stub"
    dim = 3

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0, 0.0] if "retrieval" in text else [0.0, 1.0, 0.0]
            for text in texts
        ]


def _sample_store(tmp_path: Path) -> tuple[GenerationStore, str, str]:
    store = GenerationStore(tmp_path / "corpus.sqlite")
    first = store.create_root("retrieval systems")
    second = store.create_root("garden planning")
    return store, first.id, second.id


def test_embed_corpus_builds_guardian_angel_compatible_index(tmp_path) -> None:
    store, first_id, second_id = _sample_store(tmp_path)

    assert embed_corpus(store.db_path, StubEmbedder(), batch_size=1) == 2
    assert vector_count(store.db_path) == 2

    with closing(store.connect()) as conn:
        load_vec(conn)
        assert read_meta(conn) == ("stub", 3)
        assert vector_search(conn, [1.0, 0.0, 0.0], 2) == [first_id, second_id]


def test_embed_corpus_incremental_adds_and_prunes_nodes(tmp_path) -> None:
    store, first_id, second_id = _sample_store(tmp_path)
    embedder = get_embedder("hash", dim=64)
    assert embed_corpus(store.db_path, embedder) == 2

    third = store.create_root("newly added text")
    with closing(store.connect()) as conn, conn:
        conn.execute("DELETE FROM nodes WHERE id = ?", (second_id,))
        conn.execute("DELETE FROM trees WHERE id = ?", (second_id,))

    assert embed_corpus(store.db_path, embedder, incremental=True) == 1
    assert vector_count(store.db_path) == 2
    with closing(store.connect()) as conn:
        load_vec(conn)
        indexed = {str(row[0]) for row in conn.execute("SELECT node_id FROM nodes_vec")}
    assert indexed == {first_id, third.id}


def test_embed_corpus_incremental_rebuilds_when_dimension_changes(tmp_path) -> None:
    store, _first_id, _second_id = _sample_store(tmp_path)
    assert embed_corpus(store.db_path, get_embedder("hash", dim=64)) == 2

    assert (
        embed_corpus(store.db_path, get_embedder("hash", dim=32), incremental=True) == 2
    )
    with closing(store.connect()) as conn:
        assert read_meta(conn) == ("hash", 32)


def test_embed_cli_builds_searchable_hash_index(tmp_path) -> None:
    store, first_id, _second_id = _sample_store(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["embed", "--db", str(store.db_path), "--model", "hash", "--dim", "64"],
    )

    assert result.exit_code == 0
    assert "Embedded 2 node(s) with hash (dim=64)" in result.output
    hits = KeywordBackend(store).search("retrieval")
    assert hits[0].best_node_id == first_id


def test_embed_corpus_validates_batch_and_text_limits(tmp_path) -> None:
    store, _first_id, _second_id = _sample_store(tmp_path)
    embedder = get_embedder("hash")

    for kwargs, message in [
        ({"batch_size": 0}, "batch_size must be positive"),
        ({"min_chars": -1}, "min_chars must be non-negative"),
    ]:
        try:
            embed_corpus(store.db_path, embedder, **kwargs)
        except ValueError as exc:
            assert str(exc) == message
        else:
            raise AssertionError("expected ValueError")
