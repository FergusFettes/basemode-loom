"""Pluggable retrieval over a loom corpus.

Today only keyword (FTS5/BM25) search is wired; semantic (sqlite-vec) search is
a planned drop-in behind the same :class:`SearchBackend` interface. Results are
rolled up from node hits to whole trees, since the tree picker is tree-level.
"""

from __future__ import annotations

from .search import (
    KeywordBackend,
    SearchBackend,
    SearchStatus,
    TreeHit,
    get_backend,
)

__all__ = [
    "KeywordBackend",
    "SearchBackend",
    "SearchStatus",
    "TreeHit",
    "get_backend",
]
