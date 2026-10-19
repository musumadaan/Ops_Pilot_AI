from decimal import Decimal

from modules.ocr.extract.invoice_schema import (
    InvoiceExtraction,
    MoneyField,
    ProcessingInfo,
    TextField,
)
from modules.ocr.pipeline import DocumentPipeline
from modules.ocr.review.corrections import (
    Correction,
    apply_corrections,
    load_corrections,
    save_correction,
)
from modules.ocr.validate.supplier_match import SupplierMaster, normalize_name, supplier_flags

MASTER = SupplierMaster(
    ids=["S1", "S2"], names=["Prairie Steel & Fabrication Ltd.", "Bowline Office Products Inc."])


def test_normalize_name_drops_suffix_and_punctuation():
    assert normalize_name("Prairie Steel & Fabrication Ltd.") == "prairie steel and fabrication"


def test_exact_and_noisy_match():
    assert MASTER.match("Prairie Steel & Fabrication Ltd.").status == "matched"
    noisy = MASTER.match("Prairie Stee1 & Fabrication Ltd")          # OCR l -> 1
    assert noisy.supplier_id == "S1" and noisy.status in {"matched", "ambiguous"}


def test_unknown_supplier_is_error_flag():
    m = MASTER.match("Totally Different Company")
    assert m.status == "unknown" and m.supplier_id is None
    assert supplier_flags(m)[0].severity == "error"


def _inv(total: str) -> InvoiceExtraction:
    return InvoiceExtraction(
        source_file="x.pdf", source_sha256="ab" * 32,
        processing=ProcessingInfo(pipeline_version="t", engine="t", used_ocr=True),
        total=MoneyField(value=Decimal(total), raw=total, confidence=0.4),
        subtotal=MoneyField(value=Decimal("100.00"), raw="100.00", confidence=0.9),
        supplier_name=TextField(value="Acme", raw="Acme", confidence=0.9),
    )


def test_correction_roundtrip_and_revalidate(tmp_path):
    inv = _inv("1000.00")                                               # wrong total
    pipe = DocumentPipeline(engine=None)
    assert "total_mismatch" in pipe.revalidate(inv).flag_codes()

    c = Correction(inv.source_sha256, "total", "1000.00", "100.00", reviewer="tester")
    store = tmp_path / "corr.jsonl"
    save_correction(c, store)
    loaded = load_corrections(store, inv.source_sha256)
    assert len(loaded) == 1 and loaded[0].reviewer == "tester"

    fixed = pipe.revalidate(apply_corrections(inv, loaded))
    assert fixed.total.value == Decimal("100.00")
    assert fixed.total.source == "human" and fixed.total.confidence == 1.0
    assert "total_mismatch" not in fixed.flag_codes()
    assert inv.total.value == Decimal("1000.00")                        # original untouched


def test_load_corrections_filters_by_document(tmp_path):
    store = tmp_path / "c.jsonl"
    save_correction(Correction("a" * 64, "total", None, "1.00", "r"), store)
    save_correction(Correction("b" * 64, "total", None, "2.00", "r"), store)
    assert len(load_corrections(store)) == 2
    assert len(load_corrections(store, "a" * 64)) == 1
    assert load_corrections(tmp_path / "missing.jsonl") == []
