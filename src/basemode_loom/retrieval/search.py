"""Query path over a loom corpus: ID, keyword, and semantic search.

``KeywordBackend`` runs the FTS5/BM25 ranker over node text (the ``nodes_fts``
index built by the guardian-angel corpus pipeline), then aggregates the ranked
node hits up to their trees — each tree scored by its best-ranking node — so the
tree picker can order trees by relevance.

If ``nodes_vec`` and ``vec_meta`` are present, the backend also reconstructs the
same embedder used to build the sqlite-vec index and fuses semantic ranking with
BM25 through reciprocal-rank fusion. ID-like queries are handled separately so a
node/session hash can find its tree even when the hash is not in indexed text.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from importlib.util import find_spec
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .embedder import get_embedder
from .vectors import load_vec, read_meta, vector_search

if TYPE_CHECKING:
    from basemode_loom.store import GenerationStore

_FTS_TABLE = "nodes_fts"
_FTS_TOKEN = re.compile(r"\w+", re.UNICODE)
_ID_TOKEN = re.compile(r"^[0-9a-fA-F]{6,64}$")
_RRF_K = 60


@dataclass(frozen=True)
class SearchStatus:
    """What a backend can do against the current database."""

    keyword: bool
    semantic: bool
    message: str = ""


@dataclass(frozen=True)
class TreeHit:
    """A tree that matched a search, with its best node and relevance score.

    ``score`` is higher-is-better (rank-derived), so callers can sort descending
    regardless of the underlying ranker's native scale.
    """

    tree_id: str
    score: float
    best_node_id: str


@runtime_checkable
class SearchBackend(Protocol):
    def status(self) -> SearchStatus: ...

    def search(self, query: str, *, limit: int = 200) -> list[TreeHit]: ...


def fts_match_query(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression (OR of quoted tokens).

    Quoting each token sidesteps FTS5 operator syntax in user input; OR keeps
    recall broad and lets BM25 reward documents covering more terms. Mirrors
    guardian-angel's ``retrieval.search.fts_match_query``.
    """
    tokens = _FTS_TOKEN.findall(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens)


class KeywordBackend:
    """Hybrid search, aggregated from node hits to trees.

    The name is kept for API compatibility with the first keyword-only version.
    """

    def __init__(self, store: GenerationStore) -> None:
        self._store = store
        index = store.search_index_status()
        self._has_fts = index["fts"]
        self._has_vec = index["vec"]

    def status(self) -> SearchStatus:
        semantic, semantic_message = self._semantic_status()
        if not self._has_fts and not semantic:
            return SearchStatus(
                keyword=False,
                semantic=semantic,
                message=semantic_message or "no search index in this database",
            )
        return SearchStatus(
            keyword=self._has_fts,
            semantic=semantic,
            message=semantic_message,
        )

    def search(self, query: str, *, limit: int = 200) -> list[TreeHit]:
        query = query.strip()
        if not query:
            return []
        pool = max(limit * 5, 200)
        rankings: list[list[str]] = []

        id_nodes = self._id_node_ids(query, pool)
        if id_nodes:
            rankings.append(id_nodes)

        match = fts_match_query(query)
        if match and self._has_fts:
            with closing(self._store.connect()) as conn:
                rows = self._fts_rows(conn, match, pool)
            if rows:
                rankings.append([str(row[0]) for row in rows])

        semantic_nodes = self._semantic_node_ids(query, pool)
        if semantic_nodes:
            rankings.append(semantic_nodes)

        if not rankings:
            return []

        ranked_nodes = _rrf_merge(rankings)
        node_ids = [node_id for node_id, _score in ranked_nodes]
        tree_of = self._store.node_tree_map(node_ids)

        best: dict[str, tuple[str, float]] = {}  # tree_id -> (node_id, score)
        for node_id, score in ranked_nodes:
            tree_id = tree_of.get(node_id)
            if tree_id is None:
                continue
            if tree_id not in best or score > best[tree_id][1]:
                best[tree_id] = (node_id, score)

        hits = [
            TreeHit(tree_id=tree_id, score=score, best_node_id=node_id)
            for tree_id, (node_id, score) in best.items()
        ]
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def _fts_rows(
        self, conn: sqlite3.Connection, match: str, pool: int
    ) -> list[tuple[str, float]]:
        try:
            return conn.execute(
                f"""
                SELECT node_id, bm25({_FTS_TABLE}) AS score
                FROM {_FTS_TABLE}
                WHERE {_FTS_TABLE} MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (match, pool),
            ).fetchall()
        except sqlite3.OperationalError:
            # Table vanished or malformed FTS query — degrade to no results.
            return []

    def _id_node_ids(self, query: str, limit: int) -> list[str]:
        token = query.strip()
        if not _ID_TOKEN.match(token):
            return []
        with closing(self._store.connect()) as conn:
            rows = conn.execute(
                """
                SELECT id, 0 AS rank FROM nodes WHERE id = ?
                UNION ALL
                SELECT id, 1 AS rank FROM nodes WHERE tree_id = ?
                UNION ALL
                SELECT id, 2 AS rank FROM nodes WHERE context_id = ?
                UNION ALL
                SELECT current_node_id AS id, 3 AS rank
                FROM trees WHERE id = ? AND current_node_id IS NOT NULL
                UNION ALL
                SELECT id, 4 AS rank FROM nodes WHERE id LIKE ? || '%'
                UNION ALL
                SELECT id, 5 AS rank FROM nodes WHERE tree_id LIKE ? || '%'
                UNION ALL
                SELECT id, 6 AS rank FROM nodes WHERE context_id LIKE ? || '%'
                ORDER BY rank
                LIMIT ?
                """,
                (token, token, token, token, token, token, token, limit),
            ).fetchall()
        return [str(row[0]) for row in rows if row[0]]

    def _semantic_status(self) -> tuple[bool, str]:
        if not self._has_vec:
            return False, ""
        if find_spec("sqlite_vec") is None:
            return False, "semantic index present; install basemode-loom[embed]"
        with closing(self._store.connect()) as conn:
            meta = read_meta(conn)
        if meta is None:
            return False, "semantic index present without vec_meta"
        model, _dim = meta
        if model != "hash" and find_spec("mlx_embeddings") is None:
            return False, "semantic index present; install basemode-loom[embed-mlx]"
        return True, ""

    def _semantic_node_ids(self, query: str, limit: int) -> list[str]:
        semantic, _message = self._semantic_status()
        if not semantic:
            return []
        try:
            with closing(self._store.connect()) as conn:
                meta = read_meta(conn)
                if meta is None:
                    return []
                model, dim = meta
                embedder = get_embedder(model, dim=dim)
                embed_query = getattr(embedder, "embed_query", None)
                query_vector = (
                    embed_query(query) if embed_query else embedder.embed([query])[0]
                )
                load_vec(conn)
                return vector_search(conn, query_vector, limit)
        except (RuntimeError, sqlite3.Error):
            return []


def _rrf_merge(rankings: list[list[str]], k: int = _RRF_K) -> list[tuple[str, float]]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        seen: set[str] = set()
        for rank, node_id in enumerate(ranking):
            if node_id in seen:
                continue
            seen.add(node_id)
            scores[node_id] += 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def get_backend(store: GenerationStore) -> SearchBackend:
    """Return the search backend for a store.

    The single seam where a future hybrid (keyword + semantic) backend will be
    selected; callers depend only on the :class:`SearchBackend` protocol.
    """
    return KeywordBackend(store)
