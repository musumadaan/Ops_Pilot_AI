"""Arithmetic reconciliation. All maths in Decimal; tolerance is explicit and configurable.

A failed check never "fixes" a number. It raises a flag so a human reviews the document.
"""
from __future__ import annotations

from decimal import Decimal

from ..extract.invoice_schema import InvoiceExtraction, ValidationFlag

CENT = Decimal("0.01")


def _q(x: Decimal) -> Decimal:
    return x.quantize(CENT)


def check_arithmetic(inv: InvoiceExtraction, tolerance: Decimal = Decimal("0.02")) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []

    # 1. each line: quantity x unit price = amount
    for item in inv.line_items:
        if item.quantity is not None and item.unit_price is not None and item.amount is not None:
            expected = _q(item.quantity * item.unit_price)
            if abs(expected - item.amount) > tolerance:
                flags.append(ValidationFlag(
                    code="line_amount_mismatch", severity="error", field=f"line_items[{item.line_no}]",
                    message=f"Line {item.line_no}: {item.quantity} x {item.unit_price} = {expected}, "
                            f"but amount is {item.amount}."))

    amounts = [i.amount for i in inv.line_items if i.amount is not None]
    line_sum = _q(sum(amounts, Decimal(0))) if amounts else None
    subtotal = inv.subtotal.value if inv.subtotal else None
    tax = inv.tax.value if inv.tax else None
    total = inv.total.value if inv.total else None

    # 2. subtotal = sum of lines
    if subtotal is not None and line_sum is not None and abs(subtotal - line_sum) > tolerance:
        flags.append(ValidationFlag(
            code="subtotal_mismatch", severity="error", field="subtotal",
            message=f"Sum of line amounts is {line_sum}, but subtotal is {subtotal}."))

    # 3. subtotal + tax = total (fall back to line sum if there is no subtotal line)
    base = subtotal if subtotal is not None else line_sum
    if base is not None and total is not None:
        expected_total = _q(base + (tax or Decimal(0)))
        if abs(expected_total - total) > tolerance:
            flags.append(ValidationFlag(
                code="total_mismatch", severity="error", field="total",
                message=f"{'Subtotal' if subtotal is not None else 'Line sum'} {base} + tax "
                        f"{tax or Decimal(0)} = {expected_total}, but total is {total}."))
        if subtotal is None:
            flags.append(ValidationFlag(
                code="subtotal_missing", severity="info", field="subtotal",
                message="No subtotal line found; total was reconciled against the line sum."))
    elif total is None:
        flags.append(ValidationFlag(code="total_missing", severity="error", field="total",
                                    message="No total amount could be read."))

    # 4. tax plausibility: negative or larger than the base
    if tax is not None and base is not None and (tax < 0 or tax > base):
        flags.append(ValidationFlag(code="tax_implausible", severity="warning", field="tax",
                                    message=f"Tax {tax} is negative or exceeds the subtotal {base}."))
    return flags
