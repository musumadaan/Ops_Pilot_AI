from datetime import date
from decimal import Decimal

from modules.ocr.extract.invoice_schema import (
    DateField,
    InvoiceExtraction,
    LineItem,
    MoneyField,
    ProcessingInfo,
    TextField,
    ValidationFlag,
)
from modules.ocr.validate.arithmetic import check_arithmetic
from modules.ocr.validate.checks import run_checks


def _money(v: str) -> MoneyField:
    return MoneyField(value=Decimal(v), raw=v, confidence=1.0)


def _text(v: str) -> TextField:
    return TextField(value=v, raw=v, confidence=1.0)


def _date(d: date) -> DateField:
    return DateField(value=d, raw=d.isoformat(), confidence=1.0)


def make(lines, subtotal, tax, total, **kw) -> InvoiceExtraction:
    return InvoiceExtraction(
        source_file="x.pdf", source_sha256="0" * 64,
        processing=ProcessingInfo(pipeline_version="t", engine="t", used_ocr=False),
        line_items=[LineItem(line_no=i + 1, description=f"item {i}", quantity=Decimal(q),
                             unit_price=Decimal(u), amount=Decimal(a))
                    for i, (q, u, a) in enumerate(lines)],
        subtotal=_money(subtotal) if subtotal else None,
        tax=_money(tax) if tax else None,
        total=_money(total) if total else None,
        invoice_number=_text("INV-1"), supplier_name=_text("Acme Ltd."),
        invoice_date=_date(date(2026, 1, 5)), currency=_text("CAD"), **kw,
    )


def codes(flags):
    return {f.code for f in flags}


def test_clean_invoice_has_no_arithmetic_flags():
    inv = make([("2", "10.00", "20.00"), ("1", "5.50", "5.50")], "25.50", "1.28", "26.78")
    assert check_arithmetic(inv) == []


def test_line_amount_mismatch():
    inv = make([("2", "10.00", "21.00")], "21.00", "0.00", "21.00")
    assert "line_amount_mismatch" in codes(check_arithmetic(inv))


def test_subtotal_mismatch():
    inv = make([("1", "10.00", "10.00")], "11.00", "0.00", "11.00")
    assert "subtotal_mismatch" in codes(check_arithmetic(inv))


def test_total_mismatch_is_error():
    inv = make([("1", "10.00", "10.00")], "10.00", "0.50", "99.00")
    flags = check_arithmetic(inv)
    assert "total_mismatch" in codes(flags)
    assert all(f.severity == "error" for f in flags if f.code == "total_mismatch")
    assert inv.model_copy(update={"flags": flags}).needs_review


def test_missing_total_flagged():
    inv = make([("1", "10.00", "10.00")], "10.00", None, None)
    assert "total_missing" in codes(check_arithmetic(inv))


def test_penny_rounding_within_tolerance():
    inv = make([("3", "0.333", "1.00")], "1.00", "0.00", "1.00")
    assert "line_amount_mismatch" not in codes(check_arithmetic(inv))


def test_required_fields_and_dates():
    inv = make([("1", "1.00", "1.00")], "1.00", None, "1.00")
    inv.invoice_number = None
    inv.due_date = _date(date(2025, 12, 1))
    found = codes(run_checks(inv, today=date(2026, 2, 1)))
    assert {"invoice_number_missing", "due_before_invoice"} <= found


def test_future_invoice_date_warns_not_errors():
    inv = make([("1", "1.00", "1.00")], "1.00", None, "1.00")
    flags = [f for f in run_checks(inv, today=date(2025, 1, 1)) if f.code == "invoice_date_future"]
    assert flags and flags[0].severity == "warning"


def test_duplicate_detection():
    seen: set[str] = set()
    a = make([("1", "1.00", "1.00")], "1.00", None, "1.00")
    b = make([("1", "1.00", "1.00")], "1.00", None, "1.00")
    assert "duplicate_invoice" not in codes(run_checks(a, seen_keys=seen))
    assert "duplicate_invoice" in codes(run_checks(b, seen_keys=seen))



def test_critical_low_confidence_requires_review():
    inv = make([], "10.00", "0.00", "10.00")
    flag = ValidationFlag(
        code="low_confidence",
        severity="warning",
        field="invoice_number",
        message="Invoice number confidence is too low.",
    )
    assert inv.model_copy(update={"flags": [flag]}).needs_review


def test_ambiguous_supplier_requires_review():
    inv = make([], "10.00", "0.00", "10.00")
    flag = ValidationFlag(
        code="supplier_ambiguous",
        severity="warning",
        field="supplier_name",
        message="Supplier match requires confirmation.",
    )
    assert inv.model_copy(update={"flags": [flag]}).needs_review


def test_noncritical_warning_alone_does_not_require_review():
    inv = make([], "10.00", "0.00", "10.00")
    flag = ValidationFlag(
        code="no_line_items",
        severity="warning",
        field="line_items",
        message="No line items were parsed.",
    )
    assert not inv.model_copy(update={"flags": [flag]}).needs_review
