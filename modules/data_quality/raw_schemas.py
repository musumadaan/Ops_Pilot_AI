"""Pandera schemas that validate the raw CSV files *before* they are loaded into PostgreSQL.

Files are read as text (``dtype=str``) so nothing is silently coerced; each column is checked with an
explicit pattern. Primary-key uniqueness is enforced per table. Foreign keys and cross-table rules are
enforced by the database and by ``modules/data_quality/rules.py``.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

try:                                   # pandera >= 0.24 exposes the pandas API here
    import pandera.pandas as pa
except ImportError:                    # older releases
    import pandera as pa

ISO_DATE = r"^\d{4}-\d{2}-\d{2}$"
OPT_DATE = r"^(\d{4}-\d{2}-\d{2})?$"
TS = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$"
OPT_TS = r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?\+00:00)?$"
NUM = r"^-?\d+(\.\d+)?$"
POS_NUM = r"^\d+(\.\d+)?$"
INT = r"^\d+$"
OPT_INT = r"^\d*$"


def col(pattern: str | None = None, allowed: list[str] | None = None) -> pa.Column:
    checks = []
    if pattern:
        checks.append(pa.Check.str_matches(pattern))
    if allowed:
        checks.append(pa.Check.isin(allowed))
    return pa.Column(str, checks=checks, nullable=False)


def table(columns: dict, unique: list[str] | None = None) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(columns, unique=unique, strict=True, coerce=False)


NONEMPTY = r"^.+$"

SCHEMAS: dict[str, pa.DataFrameSchema] = {
    "supplier": table({
        "supplier_id": col(r"^SUP\d{4}$"), "name": col(NONEMPTY), "country": col(r"^[A-Z]{2}$"),
        "payment_terms_days": col(INT), "status": col(allowed=["active", "on_hold", "inactive"]),
        "created_on": col(ISO_DATE)}, unique=["supplier_id"]),
    "supplier_bank_account": table({
        "bank_account_id": col(NONEMPTY), "supplier_id": col(r"^SUP\d{4}$"), "institution": col(NONEMPTY),
        "account_last4": col(r"^\d{4}$"), "valid_from": col(ISO_DATE), "valid_to": col(OPT_DATE)},
        unique=["bank_account_id"]),
    "employee": table({
        "employee_id": col(r"^E\d{3}$"), "name": col(NONEMPTY),
        "role": col(allowed=["buyer", "ap_analyst", "manager", "director", "executive"]),
        "manager_id": col(r"^(E\d{3})?$"), "approval_limit": col(POS_NUM)}, unique=["employee_id"]),
    "contract": table({
        "contract_id": col(NONEMPTY), "supplier_id": col(r"^SUP\d{4}$"), "category": col(NONEMPTY),
        "effective_from": col(ISO_DATE), "effective_to": col(OPT_DATE), "price_tolerance_pct": col(POS_NUM)},
        unique=["contract_id"]),
    "purchase_order": table({
        "po_number": col(r"^PO-\d{6}$"), "supplier_id": col(r"^SUP\d{4}$"), "buyer_id": col(r"^E\d{3}$"),
        "category": col(NONEMPTY), "currency": col(r"^[A-Z]{3}$"), "order_date": col(ISO_DATE),
        "status": col(allowed=["open", "closed", "cancelled"]), "cost_center": col(NONEMPTY)},
        unique=["po_number"]),
    "po_line": table({
        "po_number": col(r"^PO-\d{6}$"), "line_no": col(INT), "description": col(NONEMPTY), "uom": col(NONEMPTY),
        "qty_ordered": col(POS_NUM), "unit_price": col(POS_NUM)}, unique=["po_number", "line_no"]),
    "goods_receipt": table({
        "receipt_id": col(r"^GR-\d{6}$"), "po_number": col(r"^PO-\d{6}$"), "received_date": col(ISO_DATE),
        "received_by": col(r"^E\d{3}$")}, unique=["receipt_id"]),
    "receipt_line": table({
        "receipt_id": col(r"^GR-\d{6}$"), "line_no": col(INT), "po_number": col(r"^PO-\d{6}$"),
        "po_line_no": col(INT), "qty_received": col(POS_NUM)}, unique=["receipt_id", "line_no"]),
    "invoice": table({
        "invoice_id": col(r"^INV-\d{6}$"), "supplier_id": col(r"^SUP\d{4}$"), "invoice_number": col(NONEMPTY),
        "po_number": col(r"^(PO-\d{6})?$"), "invoice_date": col(ISO_DATE), "received_at": col(TS),
        "approved_at": col(OPT_TS), "due_date": col(ISO_DATE), "currency": col(r"^[A-Z]{3}$"),
        "subtotal": col(POS_NUM), "tax": col(POS_NUM), "total": col(POS_NUM), "bank_account_id": col(r"^.*$"),
        "status": col(allowed=["received", "extracted", "matched", "exception", "approved", "paid", "rejected"]),
        "source": col(allowed=["ocr", "edi", "manual"]), "source_sha256": col(r"^([0-9a-f]{64})?$")},
        unique=["invoice_id"]),
    "invoice_line": table({
        "invoice_id": col(r"^INV-\d{6}$"), "line_no": col(INT), "po_number": col(r"^(PO-\d{6})?$"),
        "po_line_no": col(OPT_INT), "description": col(NONEMPTY), "qty": col(NUM), "unit_price": col(NUM),
        "amount": col(NUM)}, unique=["invoice_id", "line_no"]),
    "payment": table({
        "payment_id": col(r"^PAY-\d{6}$"), "invoice_id": col(r"^INV-\d{6}$"), "paid_date": col(ISO_DATE),
        "amount": col(POS_NUM), "bank_account_id": col(r"^.*$"), "method": col(allowed=["eft", "cheque", "wire"])},
        unique=["payment_id"]),
    "budget": table({"category": col(NONEMPTY), "period": col(r"^\d{4}-\d{2}-01$"), "amount": col(POS_NUM)},
                    unique=["category", "period"]),
    "user_supplier_access": table({"user_name": col(NONEMPTY), "supplier_id": col(r"^SUP\d{4}$")},
                                  unique=["user_name", "supplier_id"]),
}


def validate_file(name: str, path: str | Path) -> int:
    """Validate one CSV against its schema; raises ``pandera.errors.SchemaErrors`` listing every failure."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    SCHEMAS[name].validate(df, lazy=True)
    return len(df)
