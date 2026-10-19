"""Sparse (BM25) and dense (LSA) indexes, plus an optional sentence-transformers encoder."""
from __future__ import annotations

import math
from collections import Counter

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer

from .documents import Chunk
from .text import tokens


class BM25:
    """Okapi BM25 (k1=1.5, b=0.75) over chunk context (title + section + text)."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(tokens(c.context)) for c in chunks]
        self.len = np.array([sum(d.values()) for d in self.docs], dtype=float)
        self.avg = self.len.mean() if len(chunks) else 0.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> np.ndarray:
        q = tokens(query)
        out = np.zeros(len(self.docs))
        for i, d in enumerate(self.docs):
            norm = self.k1 * (1 - self.b + self.b * self.len[i] / (self.avg or 1.0))
            for t in q:
                f = d.get(t)
                if f:
                    out[i] += self.idf[t] * f * (self.k1 + 1) / (f + norm)
        return out


class LsaEncoder:
    """TF-IDF -> truncated SVD. A dependency-free stand-in for a neural sentence encoder (no model download needed)."""
    name = "lsa"

    def __init__(self, chunks: list[Chunk], dim: int = 48, seed: int = 0):
        self.vec = TfidfVectorizer(tokenizer=tokens, lowercase=False, token_pattern=None, ngram_range=(1, 2), sublinear_tf=True)
        X = self.vec.fit_transform([c.context for c in chunks])
        k = max(2, min(dim, X.shape[0] - 1, X.shape[1] - 1))
        self.svd = TruncatedSVD(n_components=k, random_state=seed).fit(X)
        self.matrix = self._norm(self.svd.transform(X))

    @staticmethod
    def _norm(a: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(a, axis=1, keepdims=True)
        return a / np.where(n == 0, 1, n)

    def scores(self, query: str) -> np.ndarray:
        q = self._norm(self.svd.transform(self.vec.transform([query])))
        return (self.matrix @ q.T).ravel()


class SentenceTransformerEncoder:
    """Optional neural encoder. Not installed or tested in this build; used automatically when
    ``sentence-transformers`` and the model weights are available (e.g. on the Mac: pip install sentence-transformers)."""
    name = "sentence-transformers"

    def __init__(self, chunks: list[Chunk], model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer  # raises ImportError when absent
        self.model = SentenceTransformer(model)
        self.matrix = self.model.encode([c.context for c in chunks], normalize_embeddings=True)

    def scores(self, query: str) -> np.ndarray:
        return self.matrix @ self.model.encode([query], normalize_embeddings=True)[0]
