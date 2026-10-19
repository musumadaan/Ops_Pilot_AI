"""Permission-aware hybrid retrieval: BM25 + dense, reciprocal-rank fusion, heuristic rerank.

Access control happens *before* ranking: the index for a role is built only from chunks that role may read. Restricted
text therefore cannot appear in results, cannot influence BM25 statistics, and cannot leak through scores. Unknown
roles get an empty index (fail closed).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .corpus import ROLE_ACCESS
from .documents import Chunk
from .index import BM25, LsaEncoder, SentenceTransformerEncoder
from .text import tokens

MODES = ("bm25", "dense", "hybrid", "hybrid_rerank")
RRF_K = 60


@dataclass
class Hit:
    chunk: Chunk
    score: float
    parts: dict[str, float] = field(default_factory=dict)


class _RoleIndex:
    def __init__(self, chunks: list[Chunk], encoder: str):
        self.chunks = chunks
        self.bm25 = BM25(chunks) if chunks else None
        self.dense = None
        if chunks:
            if encoder in ("auto", "sentence-transformers"):
                try:
                    self.dense = SentenceTransformerEncoder(chunks)
                except Exception:
                    if encoder == "sentence-transformers":
                        raise
            self.dense = self.dense or LsaEncoder(chunks)


class Retriever:
    def __init__(self, chunks: list[Chunk], encoder: str = "lsa"):
        self.all_chunks = chunks
        self.encoder = encoder
        self._cache: dict[frozenset[str], _RoleIndex] = {}

    def _index(self, role: str) -> _RoleIndex:
        allowed = frozenset(ROLE_ACCESS.get(role, set()))
        if allowed not in self._cache:
            self._cache[allowed] = _RoleIndex([c for c in self.all_chunks if c.access in allowed], self.encoder)
        return self._cache[allowed]

    @staticmethod
    def _ranks(scores: np.ndarray) -> dict[int, int]:
        order = np.argsort(-scores, kind="stable")
        return {int(i): r for r, i in enumerate(order) if scores[i] > 0}

    def search(self, query: str, role: str, k: int = 5, mode: str = "hybrid_rerank", pool: int = 20) -> list[Hit]:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        idx = self._index(role)
        if not idx.chunks or not query.strip():
            return []
        b = idx.bm25.scores(query)
        d = idx.dense.scores(query)
        if mode == "bm25":
            order = np.argsort(-b, kind="stable")[:k]
            return [Hit(idx.chunks[i], float(b[i]), {"bm25": float(b[i])}) for i in order if b[i] > 0]
        if mode == "dense":
            order = np.argsort(-d, kind="stable")[:k]
            return [Hit(idx.chunks[i], float(d[i]), {"dense": float(d[i])}) for i in order if d[i] > 0]
        rb, rd = self._ranks(b), self._ranks(d)
        fused = {i: (1 / (RRF_K + rb[i]) if i in rb else 0.0) + (1 / (RRF_K + rd[i]) if i in rd else 0.0) for i in set(rb) | set(rd)}
        cand = sorted(fused, key=lambda i: -fused[i])[:pool]
        if mode == "hybrid":
            return [Hit(idx.chunks[i], fused[i], {"rrf": fused[i]}) for i in cand[:k]]
        top = max(fused[i] for i in cand) if cand else 1.0
        q = set(tokens(query))
        hits = []
        for i in cand:
            c = idx.chunks[i]
            body = set(tokens(c.context))
            head = set(tokens(f"{c.title} {c.section}"))
            cov = len(q & body) / len(q) if q else 0.0
            hov = len(q & head) / len(q) if q else 0.0
            s = 0.5 * fused[i] / top + 0.35 * cov + 0.15 * hov
            hits.append(Hit(c, s, {"rrf": fused[i], "coverage": cov, "heading_overlap": hov}))
        return sorted(hits, key=lambda h: -h.score)[:k]
