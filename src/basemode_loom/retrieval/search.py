"""Query path over a loom corpus: keyword search, rolled up to trees.

``KeywordBackend`` runs the FTS5/BM25 ranker over node text (the ``nodes_fts``
index built by the guardian-angel corpus pipeline), then aggregates the ranked
node hits up to their trees — each tree scored by its best-ranking node — so the
tree picker can order trees by relevance.

Semantic (sqlite-vec) search is intentionally not implemented yet: loom's
environment lacks both ``sqlite_vec`` and an MLX embedder, and a hashing
fallback would not match the corpus's mlx-built vectors. The :class:`SearchBackend`
protocol and :func:`get_backend` seam exist so a hybrid backend can be added
later without touching the picker model or UI.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from basemode_loom.store import GenerationStore

_FTS_TABLE = "nodes_fts"
_FTS_TOKEN = re.compile(r"\w+", re.UNICODE)


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
    """FTS5/BM25 keyword search, aggregated from node hits to trees."""

    def __init__(self, store: GenerationStore) -> None:
        self._store = store
        index = store.search_index_status()
        self._has_fts = index["fts"]
        self._has_vec = index["vec"]

    def status(self) -> SearchStatus:
        if not self._has_fts:
            return SearchStatus(
                keyword=False,
                semantic=False,
                message="no search index in this database",
            )
        message = "" if not self._has_vec else "semantic search not yet wired"
        return SearchStatus(keyword=True, semantic=False, message=message)

    def search(self, query: str, *, limit: int = 200) -> list[TreeHit]:
        match = fts_match_query(query)
        if not match or not self._has_fts:
            return []
        # Pull a generous pool of node hits, then collapse to trees. bm25 is
        # more-negative-is-better; the first row per tree is its best node.
        pool = max(limit * 5, 200)
        with closing(self._store.connect()) as conn:
            rows = self._fts_rows(conn, match, pool)
        if not rows:
            return []

        node_ids = [str(row[0]) for row in rows]
        tree_of = self._store.node_tree_map(node_ids)

        best: dict[str, tuple[str, int]] = {}  # tree_id -> (node_id, rank)
        for rank, node_id in enumerate(node_ids):
            tree_id = tree_of.get(node_id)
            if tree_id is None or tree_id in best:
                continue
            best[tree_id] = (node_id, rank)

        hits = [
            TreeHit(tree_id=tree_id, score=1.0 / (1 + rank), best_node_id=node_id)
            for tree_id, (node_id, rank) in best.items()
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


def get_backend(store: GenerationStore) -> SearchBackend:
    """Return the search backend for a store.

    The single seam where a future hybrid (keyword + semantic) backend will be
    selected; callers depend only on the :class:`SearchBackend` protocol.
    """
    return KeywordBackend(store)
