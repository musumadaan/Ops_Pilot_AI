"""Workflow 1 -> Workflow 2 bridge: persist a validated ``InvoiceExtraction`` into ``ops.invoice``.

Rules: only extractions with a matched supplier and every required field become invoices (status
'extracted', source 'ocr'); everything else is refused with reasons so it goes to human review rather
than being guessed into the ledger. Invoice lines are linked to PO lines by description similarity.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from sqlalchemy import Engine, text

if TYPE_CHECKING:  # Workflow 1 is an optional companion, not a dependency
    from modules.ocr.extract.invoice_schema import InvoiceExtraction

MIN_LINE_SIMILARITY = 0.6


@dataclass
class LoadOutcome:
    loaded: bool
    invoice_id: str | None = None
    reasons: list[str] = field(default_factory=list)
    unlinked_lines: list[int] = field(default_factory=list)


def _required_problems(x: InvoiceExtraction) -> list[str]:
    out = []
    if not x.supplier_match or x.supplier_match.status != "matched" or not x.supplier_match.supplier_id:
        out.append("supplier not confidently matched")
    for name in ("invoice_number", "invoice_date", "due_date", "subtotal", "tax", "total"):
        if getattr(x, name) is None:
            out.append(f"missing {name}")
    if x.invoice_date is not None and x.invoice_date.ambiguous:
        out.append("invoice_date is ambiguous (day/month)")
    out.extend(f"validation error: {f.code}" for f in x.flags if f.severity == "error")
    return out


def _link_line(desc: str, po_lines: list[dict]) -> int | None:
    best, best_s = None, 0.0
    for pl in po_lines:
        s = SequenceMatcher(None, desc.lower(), pl["description"].lower()).ratio()
        if s > best_s:
            best, best_s = pl["line_no"], s
    return best if best_s >= MIN_LINE_SIMILARITY else None


def load_extraction(engine: Engine, x: InvoiceExtraction, *, invoice_id: str, source_sha256: str | None = None,
                    received_at: datetime | None = None) -> LoadOutcome:
    source_sha256 = source_sha256 or x.source_sha256
    problems = _required_problems(x)
    if problems:
        return LoadOutcome(False, reasons=problems)
    supplier_id = x.supplier_match.supplier_id
    po = x.po_number.value if x.po_number else None
    received_at = received_at or datetime.now(UTC)
    with engine.begin() as conn:
        if po and not conn.execute(text("SELECT 1 FROM ops.purchase_order WHERE po_number=:p"), {"p": po}).first():
            po = None  # unknown PO: store as no-PO so the NO_PO exception rule fires, do not invent a link
        if conn.execute(text("SELECT 1 FROM ops.invoice WHERE invoice_id=:i"), {"i": invoice_id}).first():
            return LoadOutcome(False, reasons=[f"invoice_id {invoice_id} already exists"])
        conn.execute(text(
            "INSERT INTO ops.invoice (invoice_id, supplier_id, invoice_number, po_number, invoice_date, received_at,"
            " due_date, currency, subtotal, tax, total, status, source, source_sha256) VALUES "
            "(:id,:sup,:num,:po,:idate,:rec,:due,:cur,:sub,:tax,:tot,'extracted','ocr',:sha)"),
            {"id": invoice_id, "sup": supplier_id, "num": x.invoice_number.value, "po": po,
             "idate": x.invoice_date.value, "rec": received_at, "due": x.due_date.value,
             "cur": (x.currency.value if x.currency else "CAD"), "sub": x.subtotal.value, "tax": x.tax.value,
             "tot": x.total.value, "sha": source_sha256})
        po_lines = []
        if po:
            po_lines = [dict(r._mapping) for r in conn.execute(
                text("SELECT line_no, description FROM ops.po_line WHERE po_number=:p"), {"p": po})]
        unlinked = []
        for li in x.line_items:
            qty = li.quantity if li.quantity is not None else Decimal(1)
            price = li.unit_price if li.unit_price is not None else (li.amount or Decimal(0))
            amount = li.amount if li.amount is not None else (qty * price).quantize(Decimal("0.01"))
            link = _link_line(li.description, po_lines) if po_lines else None
            if po_lines and link is None:
                unlinked.append(li.line_no)
            conn.execute(text(
                "INSERT INTO ops.invoice_line (invoice_id, line_no, po_number, po_line_no, description, qty, unit_price, amount)"
                " VALUES (:i,:n,:po,:pl,:d,:q,:p,:a)"),
                {"i": invoice_id, "n": li.line_no, "po": po if link else None, "pl": link, "d": li.description,
                 "q": qty, "p": price, "a": amount})
        conn.execute(text(
            "INSERT INTO ops.audit_event (actor, action, entity, entity_id, detail) VALUES "
            "('wf1', 'invoice_loaded', 'invoice', :i, CAST(:d AS jsonb))"),
            {"i": invoice_id, "d": json.dumps({"engine": x.processing.engine, "pipeline": x.processing.pipeline_version,
                                                "unlinked_lines": unlinked})})
    return LoadOutcome(True, invoice_id, unlinked_lines=unlinked)
