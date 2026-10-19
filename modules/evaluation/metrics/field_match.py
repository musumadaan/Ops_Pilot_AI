"""Compare an ``InvoiceExtraction`` with synthetic ground truth.

Reports per-field exact match, line-item cell F1 (quantity, unit price, amount as one cell
group per row), description similarity, and validator behaviour on injected defects.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz import fuzz

from modules.ocr.extract.invoice_schema import InvoiceExtraction

TEXT_FIELDS = ("invoice_number", "po_number", "currency")
DATE_FIELDS = ("invoice_date", "due_date")
MONEY_FIELDS = ("subtotal", "tax", "total")
ALL_FIELDS = ("invoice_number", "invoice_date", "due_date", "po_number", "supplier_name",
              "currency", "subtotal", "tax", "total")
ARITH_FLAGS = {"line_amount_mismatch", "subtotal_mismatch", "total_mismatch"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().casefold())


@dataclass
class DocScore:
    fields: dict[str, bool] = field(default_factory=dict)
    line_tp: int = 0
    line_fp: int = 0
    line_fn: int = 0
    desc_ratio: float | None = None
    defect: str | None = None
    defect_caught: bool | None = None       # only for defect invoices
    false_arith_flag: bool | None = None    # only for clean invoices
    needs_review: bool = False


def _row_key(q, u, a) -> tuple:
    return (None if q is None else Decimal(str(q)), None if u is None else Decimal(str(u)),
            None if a is None else Decimal(str(a)))


def score(inv: InvoiceExtraction, gt: dict) -> DocScore:
    s = DocScore(defect=gt.get("defect"), needs_review=inv.needs_review)

    for name in TEXT_FIELDS:
        pred = getattr(inv, name)
        s.fields[name] = pred is not None and pred.value.strip() == gt[name]
    s.fields["supplier_name"] = (inv.supplier_name is not None
                                 and _norm(inv.supplier_name.value) == _norm(gt["supplier_name"]))
    for name in DATE_FIELDS:
        pred = getattr(inv, name)
        s.fields[name] = pred is not None and pred.value.isoformat() == gt[name]
    for name in MONEY_FIELDS:
        pred = getattr(inv, name)
        s.fields[name] = pred is not None and pred.value == Decimal(gt[name])

    gt_rows = [(_row_key(r["quantity"], r["unit_price"], r["amount"]), r["description"]) for r in gt["line_items"]]
    pr_rows = [(_row_key(i.quantity, i.unit_price, i.amount), i.description) for i in inv.line_items]
    gt_counter, pr_counter = Counter(k for k, _ in gt_rows), Counter(k for k, _ in pr_rows)
    s.line_tp = sum((gt_counter & pr_counter).values())
    s.line_fp = sum(pr_counter.values()) - s.line_tp
    s.line_fn = sum(gt_counter.values()) - s.line_tp
    ratios, used = [], Counter()
    for k, d_pred in pr_rows:
        if used[k] < gt_counter.get(k, 0):
            d_gt = [d for kk, d in gt_rows if kk == k][used[k]]
            used[k] += 1
            ratios.append(fuzz.ratio(_norm(d_pred), _norm(d_gt)) / 100)
    s.desc_ratio = sum(ratios) / len(ratios) if ratios else None

    found = inv.flag_codes()
    expected = set(gt.get("expected_flags", []))
    if expected:
        s.defect_caught = expected <= found
    else:
        s.false_arith_flag = bool(found & ARITH_FLAGS)
    return s


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f
