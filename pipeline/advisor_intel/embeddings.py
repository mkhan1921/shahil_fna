"""Text embeddings for retrieval, the topic index and nearest-key lookups (spec §1.3, §6.5, §8.3).

The production embedder is EmbeddingGemma 2 or Qwen3-Embedding-0.6B through sentence-transformers,
truncated to 256-d Matryoshka vectors, run in-process on the worker CPU. Without the optional
``embeddings`` extra (or without the model weights) the pipeline falls back to a deterministic
hashed embedder: word and character n-gram features hashed into 256 dimensions. It is weaker on
paraphrase but stable, fast and good enough for tests and offline runs. The embedder's name is
pinned per run (``VersionPins.embedding_model``) so cached vectors are never mixed across models.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from functools import lru_cache
from typing import Protocol, Sequence

import numpy as np

DIM = 256
HASHED = "hashed-256"

# Per-model instructions: queries and documents are encoded differently (§13.3).
_INSTRUCTIONS: dict[str, tuple[str, str]] = {
    "google/embeddinggemma-2-270m": ("task: search result | query: ", "title: none | text: "),
    "Qwen/Qwen3-Embedding-0.6B": ("Instruct: Given a question about a client's finances, retrieve the call segments that answer it\nQuery: ", ""),
}


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray: ...


def _normalise_rows(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    return _WORD.findall(text)


class HashedEmbedder:
    """Deterministic hashed bag of words, word bigrams and character trigrams."""

    name = HASHED
    dim = DIM

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float64)
        toks = _tokens(text)
        feats: list[tuple[str, float]] = [(f"w:{t}", 1.0) for t in toks]
        feats += [(f"b:{a}_{b}", 0.7) for a, b in zip(toks, toks[1:])]
        for t in toks:
            padded = f"#{t}#"
            feats += [(f"c:{padded[i:i + 3]}", 0.35) for i in range(max(1, len(padded) - 2))]
        for f, w in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            v[idx] += sign * w
        return v

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _normalise_rows(np.stack([self._vec(t) for t in texts]))

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        return self.embed_documents(texts)


class SentenceTransformerEmbedder:
    """EmbeddingGemma 2 / Qwen3-Embedding via sentence-transformers, truncated to 256-d (Matryoshka)."""

    def __init__(self, model_name: str, dim: int = DIM) -> None:
        from sentence_transformers import SentenceTransformer  # optional extra

        self.name = f"{model_name}@{dim}"
        self.dim = dim
        self._model = SentenceTransformer(model_name, truncate_dim=dim)
        self._query_prefix, self._doc_prefix = _INSTRUCTIONS.get(model_name, ("", ""))

    def _encode(self, texts: Sequence[str], prefix: str) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        out = self._model.encode([prefix + t for t in texts], normalize_embeddings=True, convert_to_numpy=True)
        return np.asarray(out, dtype=np.float32)

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode(texts, self._doc_prefix)

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode(texts, self._query_prefix)


@lru_cache(maxsize=4)
def get_embedder(name: str = HASHED) -> Embedder:
    """The embedder for a pinned name. Unknown or unavailable models fall back to the hashed embedder
    only when the pin asks for it; a run pinned to a real model fails loudly instead of silently
    mixing vector spaces."""
    if name == HASHED:
        return HashedEmbedder()
    model_name, _, dim = name.partition("@")
    return SentenceTransformerEmbedder(model_name, int(dim) if dim else DIM)


def cosine(queries: np.ndarray, docs: np.ndarray) -> np.ndarray:
    """Cosine similarity matrix for L2-normalised rows (queries × docs)."""
    if queries.size == 0 or docs.size == 0:
        return np.zeros((queries.shape[0], docs.shape[0]), dtype=np.float32)
    return queries @ docs.T


def nearest(query: str, keys: Sequence[str], embedder: Embedder | None = None, k: int = 3) -> list[tuple[str, float]]:
    """The ``k`` keys most similar to ``query`` (used for lexicon near-misses and expense-catalog hints)."""
    if not keys:
        return []
    emb = embedder or get_embedder()
    sims = cosine(emb.embed_queries([query]), emb.embed_documents(list(keys)))[0]
    order = np.argsort(-sims)[:k]
    return [(keys[i], float(sims[i])) for i in order]
