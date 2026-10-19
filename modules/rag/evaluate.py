"""Retrieval and answer evaluation against the golden set, with an ablation across retrieval modes.

Caveat: the golden set is small (26 answerable, 4 unanswerable, 4 access probes) and the abstention threshold was
chosen while looking at it. Treat the numbers as a regression baseline for this corpus, not as general accuracy.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .answer import answer, verify_grounded
from .corpus import GOLDEN, RESTRICTED_PROBES, UNANSWERABLE
from .documents import load_corpus
from .retriever import MODES, Retriever


def _rank_of(hits, doc_id: str, section: str) -> int | None:
    for i, h in enumerate(hits):
        if h.chunk.doc_id == doc_id and h.chunk.section == section:
            return i + 1
    return None


def retrieval_metrics(retriever: Retriever, mode: str, ks=(1, 3, 5)) -> dict[str, float]:
    ranks = []
    for q, doc, sec, role in GOLDEN:
        ranks.append(_rank_of(retriever.search(q, role, k=max(ks), mode=mode), doc, sec))
    n = len(ranks)
    out = {f"recall@{k}": sum(1 for r in ranks if r and r <= k) / n for k in ks}
    out["mrr"] = sum(1 / r for r in ranks if r) / n
    out["ndcg@5"] = sum(1 / math.log2(r + 1) for r in ranks if r and r <= 5) / n         # one relevant item: IDCG = 1
    return out


def answer_metrics(retriever: Retriever, chunks) -> dict[str, float]:
    by_id = {c.chunk_id: c for c in chunks}
    correct = grounded = 0
    for q, doc, sec, role in GOLDEN:
        a = answer(retriever, q, role)
        if not a.abstained and a.citations[0].doc_id == doc and a.citations[0].section == sec:
            correct += 1
        grounded += verify_grounded(a, by_id)
    abst = sum(answer(retriever, q, "finance_admin").abstained for q in UNANSWERABLE)
    leaks, safe_abstain = 0, 0
    restricted_text = {c.chunk_id: c for c in chunks if c.access == "finance_restricted"}
    for q, role in RESTRICTED_PROBES:
        a = answer(retriever, q, role)
        leaks += sum(c.chunk_id in restricted_text for c in a.citations)
        leaks += any(s.text[:40] in a.text for s in restricted_text.values())
        for h in retriever.search(q, role, k=10):
            leaks += h.chunk.access == "finance_restricted"
        safe_abstain += a.abstained
    return {"citation_correct_rate": correct / len(GOLDEN), "grounded_rate": grounded / len(GOLDEN),
            "abstain_rate_unanswerable": abst / len(UNANSWERABLE), "access_leaks": float(leaks),
            "restricted_probe_abstain_rate": safe_abstain / len(RESTRICTED_PROBES)}


def run(corpus_dir: str | Path = "datasets/synthetic/policies", out: str | Path | None = None) -> dict:
    chunks = load_corpus(corpus_dir)
    r = Retriever(chunks)
    report = {"chunks": len(chunks), "ablation": {m: retrieval_metrics(r, m) for m in MODES}, "answers": answer_metrics(r, chunks)}
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    print(json.dumps(run(out="modules/rag/reports/rag_eval.json"), indent=2))
