"""Typed per-invoice reconciliation with provenance, built from the SQL views (single source of truth).

The agent layer (Workflow 5) and the review UI call this; they never recompute match rules in Python.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Engine, text


class ExceptionItem(BaseModel):
    code: str
    severity: str
    occurrences: int
    detail: str | None = None
    recommended_action: str | None = None


class LineMatch(BaseModel):
    line_no: int
    po_line_no: int | None = None
    invoiced_qty: Decimal | None = None
    cumulative_invoiced_qty: Decimal | None = None
    ordered_qty: Decimal | None = None
    received_qty: Decimal | None = None
    price_variance_pct: Decimal | None = None
    amount: Decimal | None = None


class InvoiceReconciliation(BaseModel):
    invoice_id: str
    supplier_id: str
    invoice_number: str
    po_number: str | None
    total: Decimal
    match_status: str
    required_approver: str | None
    exceptions: list[ExceptionItem]
    lines: list[LineMatch]
    provenance: dict[str, Any]


def reconcile_invoice(engine: Engine, invoice_id: str) -> InvoiceReconciliation | None:
    with engine.connect() as conn:
        inv = conn.execute(text(
            "SELECT i.invoice_id, i.supplier_id, i.invoice_number, i.po_number, i.total, po.buyer_id "
            "FROM ops.invoice i LEFT JOIN ops.purchase_order po USING (po_number) WHERE i.invoice_id = :i"),
            {"i": invoice_id}).mappings().first()
        if inv is None:
            return None
        status = conn.execute(text("SELECT match_status FROM ops.v_invoice_match_status WHERE invoice_id=:i"),
                              {"i": invoice_id}).scalar()
        exc = conn.execute(text(
            "SELECT e.exception_code, e.severity, e.occurrences, e.detail, c.recommended_action "
            "FROM ops.v_invoice_exceptions e JOIN ops.exception_code c ON c.code = e.exception_code "
            "WHERE e.invoice_id = :i ORDER BY CASE e.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, 1"),
            {"i": invoice_id}).fetchall()
        lines = conn.execute(text(
            "SELECT * FROM ops.v_invoice_line_match WHERE invoice_id = :i ORDER BY line_no"), {"i": invoice_id}).mappings().all()
        approver = None
        if inv["buyer_id"]:
            approver = conn.execute(text("SELECT ops.required_approver(:b, :a)"),
                                    {"b": inv["buyer_id"], "a": inv["total"]}).scalar()
    return InvoiceReconciliation(
        invoice_id=inv["invoice_id"], supplier_id=inv["supplier_id"], invoice_number=inv["invoice_number"],
        po_number=inv["po_number"], total=inv["total"], match_status=status or "rejected",
        required_approver=approver,
        exceptions=[ExceptionItem(code=r[0], severity=r[1], occurrences=r[2], detail=r[3], recommended_action=r[4]) for r in exc],
        lines=[LineMatch(line_no=l["line_no"], po_line_no=l["po_line_no"], invoiced_qty=l["qty"],
                         cumulative_invoiced_qty=l["qty_invoiced_cum"], ordered_qty=l["qty_ordered"],
                         received_qty=l["qty_received"], price_variance_pct=l["price_variance_pct"],
                         amount=l["amount"]) for l in lines],
        provenance={"source": "ops.v_invoice_exceptions + ops.v_invoice_line_match",
                    "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds")})
