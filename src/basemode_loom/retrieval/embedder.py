"""Query embedders for semantic corpus search.

This mirrors the embedder contract used to build the Guardian Angel corpus
index: documents were embedded with ``embed`` and queries should use
``embed_query`` so asymmetric retrieval models get the right task prefix.
"""

from __future__ import annotations

import hashlib
import math
import re

DEFAULT_MLX_MODEL = "mlx-community/nomicai-modernbert-embed-base-bf16"
DEFAULT_HASH_DIM = 256
_TOKEN = re.compile(r"\w+", re.UNICODE)
_TASK_PREFIXES: dict[str, tuple[str, str]] = {
    "modernbert-embed": ("search_query: ", "search_document: "),
    "nomic": ("search_query: ", "search_document: "),
    "e5": ("query: ", "passage: "),
}


def prefixes_for(model_id: str) -> tuple[str, str]:
    low = model_id.lower()
    for key, pair in _TASK_PREFIXES.items():
        if key in low:
            return pair
    return ("", "")


class HashingEmbedder:
    """Deterministic fallback embedder used for tests and hash-built indexes."""

    def __init__(self, dim: int = DEFAULT_HASH_DIM):
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim
        self.name = "hash"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            h = int.from_bytes(digest, "big")
            vec[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vec))
        if norm == 0.0:
            return vec
        return [value / norm for value in vec]


class MlxEmbedder:
    """Local MLX sentence embedder. The model is loaded lazily on first query."""

    def __init__(
        self,
        model: str = DEFAULT_MLX_MODEL,
        *,
        max_length: int = 512,
        query_prefix: str | None = None,
        document_prefix: str | None = None,
    ):
        self.name = model
        self.max_length = max_length
        default_query, default_document = prefixes_for(model)
        self.query_prefix = default_query if query_prefix is None else query_prefix
        self.document_prefix = (
            default_document if document_prefix is None else document_prefix
        )
        self._model = None
        self._tokenizer = None
        self._dim: int | None = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            from mlx_embeddings.utils import load
        except ModuleNotFoundError as exc:  # pragma: no cover - optional extra
            raise RuntimeError(
                "semantic search needs mlx-embeddings; install basemode-loom[embed-mlx]"
            ) from exc
        self._model, self._tokenizer = load(self.name)

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self._encode(["probe"])[0])
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._encode([self.document_prefix + text for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._encode([self.query_prefix + text])[0]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        self._ensure_loaded()
        inputs = self._tokenizer.batch_encode_plus(
            list(texts),
            return_tensors="mlx",
            padding=True,
            truncation=True,
            max_length=self.max_length,
        )
        outputs = self._model(
            inputs["input_ids"], attention_mask=inputs["attention_mask"]
        )
        return [[float(value) for value in row] for row in outputs.text_embeds.tolist()]


def get_embedder(spec: str, *, dim: int | None = None):
    if spec == "hash":
        return HashingEmbedder(dim=dim or DEFAULT_HASH_DIM)
    if spec == "mlx":
        return MlxEmbedder(DEFAULT_MLX_MODEL)
    return MlxEmbedder(spec)
