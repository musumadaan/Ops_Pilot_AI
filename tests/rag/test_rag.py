from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from modules.rag import api as rag_api
from modules.rag.answer import answer, verify_grounded
from modules.rag.corpus import DOCS, RESTRICTED_PROBES, ROLE_ACCESS, UNANSWERABLE, write_corpus
from modules.rag.documents import load_corpus, load_document, parse_front_matter
from modules.rag.evaluate import answer_metrics, retrieval_metrics
from modules.rag.index import BM25
from modules.rag.retriever import MODES, Retriever
from modules.rag.text import stem, tokens

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("pol")
    write_corpus(d)
    return d


@pytest.fixture(scope="module")
def chunks(corpus_dir):
    return load_corpus(corpus_dir)


@pytest.fixture(scope="module")
def retriever(chunks):
    return Retriever(chunks)


def test_chunks_have_metadata_and_exact_spans(corpus_dir, chunks):
    raw = {p.stem: p.read_text() for p in corpus_dir.glob("*.md")}
    assert len(chunks) >= sum(len(d[5]) for d in DOCS)
    for c in chunks:
        assert c.access in {"public", "internal", "finance_restricted"} and c.version
        assert raw[c.doc_id][c.start:c.end].split() == c.text.split() or c.text.replace("  ", " ") in raw[c.doc_id]


def test_long_sections_are_split_with_overlap(tmp_path):
    long_text = " ".join(f"Sentence number {i} talks about invoices and payments." for i in range(60))
    (tmp_path / "d.md").write_text(f'---\ndoc_id: D1\ntitle: T\naccess: public\nversion: "1"\n---\n\n## S\n\n{long_text}\n')
    cs = load_document(tmp_path / "d.md")
    assert len(cs) > 1 and all(len(c.text.split()) <= 140 for c in cs)
    assert cs[0].text.split(".")[-2].strip() in cs[1].text                      # one-sentence overlap


def test_bad_front_matter_rejected():
    with pytest.raises(ValueError):
        parse_front_matter("# no front matter")
    with pytest.raises(ValueError):
        parse_front_matter("---\ndoc_id: X\n---\nbody")


def test_tokeniser_and_bm25_rank_exact_match_first(chunks):
    assert stem("approvals") == stem("approval") and "the" not in tokens("the approval")
    b = BM25(chunks)
    s = b.scores("price tolerance 2 percent purchase order")
    assert chunks[int(s.argmax())].section in {"Price tolerance", "Contracts and price tolerance"}


@pytest.mark.parametrize("mode", MODES)
def test_every_mode_returns_ordered_results(retriever, mode):
    hits = retriever.search("what is the approval threshold", "analyst", k=5, mode=mode)
    assert hits and all(a.score >= b.score for a, b in zip(hits, hits[1:]))


def test_retrieval_quality_floor(retriever):
    m = retrieval_metrics(retriever, "hybrid")
    assert m["recall@5"] >= 0.95 and m["mrr"] >= 0.8


def test_hybrid_not_worse_than_bm25_alone(retriever):
    assert retrieval_metrics(retriever, "hybrid")["mrr"] >= retrieval_metrics(retriever, "bm25")["mrr"] - 0.02


def test_answers_are_grounded_cited_and_mostly_correct(retriever, chunks):
    m = answer_metrics(retriever, chunks)
    assert m["grounded_rate"] == 1.0 and m["citation_correct_rate"] >= 0.65         # regression floor; paraphrased questions are the known weak spot


def test_abstains_on_unanswerable(retriever):
    for q in UNANSWERABLE:
        a = answer(retriever, q, "finance_admin")
        assert a.abstained and not a.citations


@pytest.mark.parametrize("role", ["guest", "analyst", "finance_admin"])
def test_results_never_exceed_role_access(retriever, role):
    for q in ("bank account change verification", "fraud investigation escalation", "approval limits", "meal allowance"):
        for mode in MODES:
            for h in retriever.search(q, role, k=10, mode=mode):
                assert h.chunk.access in ROLE_ACCESS[role]


def test_restricted_probes_do_not_leak_or_answer(retriever, chunks):
    restricted = {c.chunk_id for c in chunks if c.access == "finance_restricted"}
    for q, role in RESTRICTED_PROBES:
        a = answer(retriever, q, role)
        assert a.abstained and not ({c.chunk_id for c in a.citations} & restricted)
    assert answer(retriever, "How do we verify new supplier bank details?", "finance_admin").citations[0].doc_id == "POL-VND-001"


def test_unknown_role_fails_closed(retriever):
    assert retriever.search("approval threshold", "hacker") == [] and answer(retriever, "approval threshold", "hacker").abstained


def test_restricted_text_does_not_change_other_roles_index(chunks):
    """Removing restricted docs entirely must leave an analyst's ranking identical (no statistic leakage)."""
    full = Retriever(chunks)
    pruned = Retriever([c for c in chunks if c.access != "finance_restricted"])
    for q in ("approval limits", "duplicate invoice", "price tolerance"):
        a = [(h.chunk.chunk_id, round(h.score, 9)) for h in full.search(q, "analyst", k=5)]
        b = [(h.chunk.chunk_id, round(h.score, 9)) for h in pruned.search(q, "analyst", k=5)]
        assert a == b


def test_tampered_answer_fails_grounding_check(retriever, chunks):
    a = answer(retriever, "What price difference is allowed between an invoice and the purchase order?", "analyst")
    by_id = {c.chunk_id: c for c in chunks}
    assert verify_grounded(a, by_id)
    a.text += " Also, payments are always approved."
    assert not verify_grounded(a, by_id)


def test_api_roles_and_validation(corpus_dir, monkeypatch):
    monkeypatch.setenv("RAG_CORPUS_DIR", str(corpus_dir))
    rag_api._retriever.cache_clear()
    c = TestClient(rag_api.app)
    assert c.get("/health").json()["chunks"] > 20
    assert c.get("/search", params={"q": "approval threshold"}).status_code == 401
    assert c.get("/search", params={"q": "approval threshold"}, headers={"X-Role": "root"}).status_code == 401
    assert c.get("/search", params={"q": "x"}, headers={"X-Role": "guest"}).status_code == 422
    r = c.post("/answer", json={"question": "How do we report suspected invoice fraud?"}, headers={"X-Role": "guest"}).json()
    assert r["abstained"] and r["citations"] == []
    r = c.post("/answer", json={"question": "How do we report suspected invoice fraud?"}, headers={"X-Role": "finance_admin"}).json()
    assert not r["abstained"] and r["citations"][0]["doc_id"] == "POL-FIN-009"
    rag_api._retriever.cache_clear()
