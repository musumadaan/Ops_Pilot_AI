"""Human review: store corrections as JSONL and apply them to an extraction.

Corrections are never discarded: they are the highest-quality labelled data the system will
ever get, and feed the evaluation sets (Workflow 7). Applied values are marked source="human".
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from ..extract.invoice_schema import DateField, InvoiceExtraction, MoneyField, TextField
from ..extract.parsing import parse_date, parse_money

MONEY_FIELDS = {"subtotal", "tax", "total"}
DATE_FIELDS = {"invoice_date", "due_date"}
TEXT_FIELDS = {"invoice_number", "po_number", "supplier_name", "currency"}


@dataclass
class Correction:
    source_sha256: str
    field: str
    old_value: str | None
    new_value: str
    reviewer: str
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat(timespec="seconds")


def save_correction(c: Correction, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(c)) + "\n")


def load_corrections(path: str | Path, source_sha256: str | None = None) -> list[Correction]:
    p = Path(path)
    if not p.exists():
        return []
    rows = [Correction(**json.loads(line)) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if source_sha256 is None or r.source_sha256 == source_sha256]


def apply_corrections(inv: InvoiceExtraction, corrections: list[Correction]) -> InvoiceExtraction:
    """Return a copy with corrected fields (later corrections win), marked source="human".

    Flags are NOT edited here. Call ``DocumentPipeline.revalidate`` afterwards so the
    arithmetic and sanity checks run again on the corrected values."""
    out = inv.model_copy(deep=True)
    for c in corrections:
        old = getattr(out, c.field, None)
        meta = dict(raw=c.new_value, confidence=1.0, source="human",
                    page=getattr(old, "page", None), bbox=getattr(old, "bbox", None))
        if c.field in MONEY_FIELDS:
            val = parse_money(c.new_value)
            if val is None:
                raise ValueError(f"Cannot parse money value {c.new_value!r} for {c.field}")
            setattr(out, c.field, MoneyField(value=Decimal(val), **meta))
        elif c.field in DATE_FIELDS:
            d, _ = parse_date(c.new_value)
            if d is None:
                raise ValueError(f"Cannot parse date {c.new_value!r} for {c.field}")
            setattr(out, c.field, DateField(value=d, **meta))
        elif c.field in TEXT_FIELDS:
            setattr(out, c.field, TextField(value=c.new_value, **meta))
        else:
            raise ValueError(f"Field {c.field!r} cannot be corrected through this API")
    return out
