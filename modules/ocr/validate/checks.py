"""Required-field, date-sanity, confidence and duplicate checks."""
from __future__ import annotations

from datetime import date, timedelta

from modules.common.hashing import stable_hash

from ..extract.invoice_schema import InvoiceExtraction, ValidationFlag

REQUIRED = ("invoice_number", "invoice_date", "supplier_name", "total")
LOW_CONFIDENCE = 0.80
CHECKED_FIELDS = ("invoice_number", "invoice_date", "due_date", "po_number", "supplier_name",
                  "subtotal", "tax", "total", "currency")


def duplicate_key(inv: InvoiceExtraction) -> str | None:
    if not (inv.supplier_name and inv.invoice_number and inv.total):
        return None
    return stable_hash(inv.supplier_name.value, inv.invoice_number.value, inv.total.value)


def run_checks(
    inv: InvoiceExtraction,
    seen_keys: set[str] | None = None,
    today: date | None = None,
    low_confidence: float = LOW_CONFIDENCE,
) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []
    today = today or date.today()

    for name in REQUIRED:
        if getattr(inv, name) is None:
            flags.append(ValidationFlag(code=f"{name}_missing", severity="error", field=name,
                                        message=f"Required field '{name}' could not be read."))

    d, due = inv.invoice_date, inv.due_date
    if d:
        if d.value > today + timedelta(days=1):
            flags.append(ValidationFlag(code="invoice_date_future", severity="warning",
                                        field="invoice_date", message=f"Invoice date {d.value} is in the future."))
        if d.value.year < 2000:
            flags.append(ValidationFlag(code="invoice_date_implausible", severity="error",
                                        field="invoice_date", message=f"Invoice date {d.value} is implausible."))
    if d and due and due.value < d.value:
        flags.append(ValidationFlag(code="due_before_invoice", severity="error", field="due_date",
                                    message=f"Due date {due.value} is before invoice date {d.value}."))
    if inv.total and inv.total.value <= 0:
        flags.append(ValidationFlag(code="total_not_positive", severity="warning", field="total",
                                    message=f"Total {inv.total.value} is zero or negative (credit note?)."))
    if not inv.line_items:
        flags.append(ValidationFlag(code="no_line_items", severity="warning", field="line_items",
                                    message="No line-item table could be parsed."))
    if inv.currency is None and "currency_ambiguous" not in inv.flag_codes():
        flags.append(ValidationFlag(code="currency_missing", severity="warning", field="currency",
                                    message="No currency found on the document."))

    for name in CHECKED_FIELDS:
        f = getattr(inv, name)
        if f is not None and f.confidence < low_confidence:
            flags.append(ValidationFlag(code="low_confidence", severity="warning", field=name,
                                        message=f"'{name}' read with confidence {f.confidence:.2f} "
                                                f"(< {low_confidence:.2f}): {f.raw!r}."))

    key = duplicate_key(inv)
    if key and seen_keys is not None:
        if key in seen_keys:
            flags.append(ValidationFlag(code="duplicate_invoice", severity="error", field="invoice_number",
                                        message="Same supplier, invoice number and total already processed."))
        seen_keys.add(key)
    return flags
