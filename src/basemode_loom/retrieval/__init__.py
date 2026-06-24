"""Pluggable retrieval over a loom corpus.

Search combines exact/prefix ID lookup, FTS5/BM25 keyword ranking, and optional
sqlite-vec semantic ranking. Results are rolled up from node hits to whole
trees, since the tree picker is tree-level.
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
