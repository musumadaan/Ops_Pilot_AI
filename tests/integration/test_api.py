import csv
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient

from modules.ocr import api

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "datasets/evaluation/ocr/manifest.csv"
client = TestClient(api.app)


def _first_pdf() -> Path:
    with open(MANIFEST, newline="", encoding="utf-8") as fh:
        row = next(r for r in csv.DictReader(fh) if r["stratum"] == "digital")
    return ROOT / row["pdf"]


def test_health():
    assert client.get("/health").json()["status"] == "ok"


@pytest.mark.skipif(not MANIFEST.exists(), reason="dataset not generated")
def test_extract_pdf_returns_contract():
    pdf = _first_pdf()
    r = client.post("/ocr/extract", files={"file": ("../../evil name.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_file"] == "evil name.pdf"                 # path components stripped
    assert body["invoice_number"]["value"] and body["total"]["value"]
    assert body["processing"]["used_ocr"] is False and len(body["source_sha256"]) == 64


def test_rejects_wrong_type_even_with_pdf_name():
    r = client.post("/ocr/extract", files={"file": ("x.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")})
    assert r.status_code == 415


def test_rejects_empty_and_oversize(monkeypatch):
    assert client.post("/ocr/extract", files={"file": ("x.pdf", b"", "application/pdf")}).status_code == 400
    monkeypatch.setattr(api, "MAX_BYTES", 10)
    r = client.post("/ocr/extract", files={"file": ("x.pdf", b"%PDF-" + b"0" * 100, "application/pdf")})
    assert r.status_code == 413


def test_corrupt_pdf_is_422_not_500():
    r = client.post("/ocr/extract", files={"file": ("x.pdf", b"%PDF-1.4 garbage", "application/pdf")})
    assert r.status_code == 422
