"""End-to-end checks on the synthetic dataset (needs datasets/ from scripts/generate_invoices.py).

* digital PDFs: no OCR, so these test the extraction rules + validators exactly
* images (marked ``ocr``): need the tesseract binary; assert the key business fields only
"""
import csv
import json
import shutil
from pathlib import Path

import pytest

from modules.evaluation.metrics.field_match import ALL_FIELDS, score
from modules.ocr.pipeline import DocumentPipeline
from modules.ocr.validate.supplier_match import SupplierMaster

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "datasets/evaluation/ocr/manifest.csv"
SUPPLIERS = ROOT / "datasets/synthetic/suppliers.csv"

pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="run the generator + degrade scripts first")


def rows(stratum: str) -> list[dict]:
    with open(MANIFEST, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r["stratum"] == stratum]


def gt_of(row: dict) -> dict:
    return json.loads((ROOT / row["gt"]).read_text())


@pytest.fixture(scope="module")
def master() -> SupplierMaster:
    return SupplierMaster.from_csv(SUPPLIERS)


def test_digital_pdfs_extract_exactly(master):
    results = []
    for r in rows("digital"):
        pipe = DocumentPipeline(engine=None, supplier_master=master)
        inv = pipe.process(ROOT / r["pdf"])
        assert inv.processing.used_ocr is False
        results.append((inv, gt_of(r), score(inv, gt_of(r))))
    assert len(results) >= 20
    for inv, gt, sc in results:
        wrong = [f for f in ALL_FIELDS if not sc.fields.get(f)]
        assert not wrong, f"{gt['id']} ({gt['layout']}): wrong fields {wrong}"
        assert sc.line_fp == 0 and sc.line_fn == 0, gt["id"]


def test_deliberate_arithmetic_defects_are_caught(master):
    defect_docs = clean_docs = 0
    for r in rows("digital"):
        gt = gt_of(r)
        inv = DocumentPipeline(engine=None, supplier_master=master).process(ROOT / r["pdf"])
        if gt.get("expected_flags"):
            defect_docs += 1
            assert set(gt["expected_flags"]) <= inv.flag_codes(), gt["id"]
            assert inv.needs_review
        else:
            clean_docs += 1
            assert not inv.needs_review, (gt["id"], inv.flag_codes())
    assert defect_docs >= 1 and clean_docs >= 1


def test_duplicate_submission_is_flagged(master):
    r = rows("digital")[0]
    pipe = DocumentPipeline(engine=None, supplier_master=master)
    first = pipe.process(ROOT / r["pdf"])
    second = pipe.process(ROOT / r["pdf"])
    assert "duplicate_invoice" not in first.flag_codes()
    assert "duplicate_invoice" in second.flag_codes()


def test_source_hash_is_stable():
    r = rows("digital")[0]
    a = DocumentPipeline(engine=None).process(ROOT / r["pdf"])
    b = DocumentPipeline(engine=None).process(ROOT / r["pdf"])
    assert a.source_sha256 == b.source_sha256 and len(a.source_sha256) == 64


def test_unsupported_file_type(tmp_path):
    bad = tmp_path / "x.txt"
    bad.write_text("hello")
    with pytest.raises(ValueError):
        DocumentPipeline(engine=None).process(bad)


needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")


@pytest.mark.ocr
@needs_tesseract
@pytest.mark.parametrize("stratum", ["clean", "skew"])
def test_ocr_on_clean_and_skewed_scans(stratum, master):
    sample = rows(stratum)[:4]
    assert sample
    hits = 0
    for r in sample:
        gt = gt_of(r)
        inv = DocumentPipeline(engine="tesseract", supplier_master=master).process(ROOT / r["image"])
        assert inv.processing.used_ocr is True
        ok = (inv.invoice_number is not None and inv.invoice_number.value == gt["invoice_number"]
              and inv.total is not None and str(inv.total.value) == gt["total"])
        hits += ok
    assert hits >= len(sample) - 1, f"{stratum}: only {hits}/{len(sample)} had correct number + total"


@pytest.mark.ocr
@needs_tesseract
@pytest.mark.parametrize("stratum", ["lowres", "noisy"])
def test_ocr_degraded_scans_never_crash_and_fail_safe(stratum, master):
    """Hard strata may misread, but the pipeline must finish and a wrong total must not pass silently."""
    for r in rows(stratum)[:3]:
        gt = gt_of(r)
        inv = DocumentPipeline(engine="tesseract", supplier_master=master).process(ROOT / r["image"])
        total_ok = inv.total is not None and str(inv.total.value) == gt["total"]
        if not total_ok:
            assert inv.needs_review or inv.flag_codes() & {"low_confidence", "total_missing"}, gt["id"]
