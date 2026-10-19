"""Permission-aware retrieval API.   uvicorn modules.rag.api:app --port 8004

The caller's role comes from the ``X-Role`` header. In production this must come from verified auth (a signed token),
never from a client-supplied header; the header here keeps the demo self-contained.
"""
from __future__ import annotations

import os
from dataclasses import asdict
from functools import lru_cache

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from .answer import answer
from .corpus import ROLE_ACCESS
from .documents import load_corpus
from .retriever import MODES, Retriever

app = FastAPI(title="OpsPilot RAG", version="0.1.0")


@lru_cache(maxsize=1)
def _retriever() -> Retriever:
    return Retriever(load_corpus(os.getenv("RAG_CORPUS_DIR", "datasets/synthetic/policies")))


def _role(x_role: str | None) -> str:
    if x_role not in ROLE_ACCESS:
        raise HTTPException(status_code=401, detail=f"unknown role; expected one of {sorted(ROLE_ACCESS)}")
    return x_role


class Ask(BaseModel):
    question: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "chunks": len(_retriever().all_chunks)}


@app.get("/search")
def search(q: str = Query(min_length=2, max_length=500), k: int = Query(5, ge=1, le=20), mode: str = "hybrid",
           x_role: str | None = Header(default=None)) -> dict:
    role = _role(x_role)
    if mode not in MODES:
        raise HTTPException(status_code=422, detail=f"mode must be one of {MODES}")
    hits = _retriever().search(q, role, k=k, mode=mode)
    return {"role": role, "results": [{"chunk_id": h.chunk.chunk_id, "doc_id": h.chunk.doc_id, "section": h.chunk.section,
                                       "version": h.chunk.version, "score": round(h.score, 4), "text": h.chunk.text} for h in hits]}


@app.post("/answer")
def ask(body: Ask, x_role: str | None = Header(default=None)) -> dict:
    role = _role(x_role)
    if not 2 <= len(body.question) <= 500:
        raise HTTPException(status_code=422, detail="question must be 2-500 characters")
    return asdict(answer(_retriever(), body.question, role))
