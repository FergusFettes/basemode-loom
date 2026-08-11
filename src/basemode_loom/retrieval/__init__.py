"""Pluggable retrieval over a loom corpus.

Search combines exact/prefix ID lookup, FTS5/BM25 keyword ranking, and optional
sqlite-vec semantic ranking. Results are rolled up from node hits to whole
trees, since the tree picker is tree-level.
"""

from __future__ import annotations

from .embedder import HashingEmbedder, MlxEmbedder, get_embedder
from .search import (
    KeywordBackend,
    SearchBackend,
    SearchStatus,
    TreeHit,
    get_backend,
)
from .vectors import embed_corpus, vector_count

__all__ = [
    "HashingEmbedder",
    "KeywordBackend",
    "MlxEmbedder",
    "SearchBackend",
    "SearchStatus",
    "TreeHit",
    "embed_corpus",
    "get_backend",
    "get_embedder",
    "vector_count",
]
