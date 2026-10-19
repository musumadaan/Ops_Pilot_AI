"""Extractive, cited answers with abstention. Nothing is generated: every sentence is copied from a retrieved chunk.

That is a deliberate choice for the no-budget build: it makes "grounded" mechanically checkable (``verify_grounded``).
An LLM can be placed on top later (Workflow 5), but only over these cited sentences, and its output re-verified.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .retriever import Retriever
from .text import sentences, tokens

MIN_COVERAGE = 0.5          # share of query terms the best chunk must contain, else abstain
MIN_BM25_HYBRID = 0.0


@dataclass
class Citation:
    doc_id: str
    title: str
    version: str
    section: str
    chunk_id: str
    quote: str
    start: int
    end: int


@dataclass
class Answer:
    question: str
    role: str
    abstained: bool
    text: str
    citations: list[Citation] = field(default_factory=list)
    reason: str = ""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def answer(retriever: Retriever, question: str, role: str, k: int = 5, max_sentences: int = 2,
           mode: str = "hybrid") -> Answer:
    hits = retriever.search(question, role, k=k, mode=mode)
    if not hits:
        return Answer(question, role, True, "I could not find this in the policies you have access to.", reason="no_results")
    top = hits[0]
    q_terms = set(tokens(question))
    cov = len(q_terms & set(tokens(top.chunk.context))) / len(q_terms) if q_terms else 0.0
    if cov < MIN_COVERAGE:
        return Answer(question, role, True, "I could not find this in the policies you have access to.",
                      reason=f"low_coverage({cov:.2f})")
    q = set(tokens(question))
    ranked = sorted(sentences(top.chunk.text), key=lambda s: -len(q & set(tokens(s))))
    chosen = [s for s in ranked[:max_sentences] if q & set(tokens(s))] or ranked[:1]
    chosen.sort(key=top.chunk.text.index)
    c = top.chunk
    cites = []
    for s in chosen:
        pos = c.text.find(s)
        start = c.start + pos if pos >= 0 else c.start
        cites.append(Citation(c.doc_id, c.title, c.version, c.section, c.chunk_id, s, start, start + len(s) if pos >= 0 else c.end))
    return Answer(question, role, False, " ".join(chosen), cites)


def verify_grounded(ans: Answer, chunks_by_id: dict[str, object]) -> bool:
    """Every cited quote must appear verbatim in its chunk and the answer text must be made only of cited quotes."""
    if ans.abstained:
        return not ans.citations
    quotes = []
    for c in ans.citations:
        chunk = chunks_by_id.get(c.chunk_id)
        if chunk is None or _norm(c.quote) not in _norm(chunk.text):
            return False
        quotes.append(_norm(c.quote))
    return _norm(ans.text) == _norm(" ".join(quotes))
