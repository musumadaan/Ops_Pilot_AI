"""Pydantic contract for Workflow 1 output. Every downstream workflow depends on this file.

Design rules
- Money is ``Decimal``, never float.
- Every extracted field carries confidence, page, a normalised bounding box (0-1, origin
  top-left) and a ``source`` so the UI can draw overlays and reviewers can see where a
  value came from.
- Validation problems are explicit ``ValidationFlag`` objects, never silent fixes.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, computed_field

Severity = Literal["error", "warning", "info"]
FieldSource = Literal["label", "heuristic", "derived", "human"]


class BBox(BaseModel):
    """Normalised box on a page: all values in [0, 1], origin top-left."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class FieldMeta(BaseModel):
    raw: str = Field(description="Text exactly as read from the document")
    confidence: float = Field(ge=0.0, le=1.0)
    page: int | None = None
    bbox: BBox | None = None
    source: FieldSource = "label"


class TextField(FieldMeta):
    value: str


class MoneyField(FieldMeta):
    value: Decimal


class DateField(FieldMeta):
    value: date
    ambiguous: bool = False


class LineItem(BaseModel):
    line_no: int
    description: str
    sku: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    page: int | None = None
    bbox: BBox | None = None


class ValidationFlag(BaseModel):
    code: str
    severity: Severity
    message: str
    field: str | None = None


class SupplierMatch(BaseModel):
    supplier_id: str | None
    master_name: str | None
    score: float
    status: Literal["matched", "ambiguous", "unknown"]


class ProcessingInfo(BaseModel):
    pipeline_version: str
    engine: str
    used_ocr: bool
    preprocess: list[str] = Field(default_factory=list)
    pages: int = 0
    mean_word_confidence: float | None = None
    elapsed_seconds: float = 0.0


class InvoiceExtraction(BaseModel):
    source_file: str
    source_sha256: str
    processing: ProcessingInfo

    invoice_number: TextField | None = None
    invoice_date: DateField | None = None
    due_date: DateField | None = None
    po_number: TextField | None = None
    supplier_name: TextField | None = None
    currency: TextField | None = None
    subtotal: MoneyField | None = None
    tax: MoneyField | None = None
    total: MoneyField | None = None

    line_items: list[LineItem] = Field(default_factory=list)
    supplier_match: SupplierMatch | None = None
    flags: list[ValidationFlag] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def needs_review(self) -> bool:
        """Whether errors or critical extraction warnings require human review."""
        critical_fields = {"invoice_number", "supplier_name", "total"}
        return any(
            f.severity == "error"
            or f.code == "supplier_ambiguous"
            or (
                f.code == "low_confidence"
                and f.field in critical_fields
            )
            for f in self.flags
        )

    def flag_codes(self) -> set[str]:
        return {f.code for f in self.flags}
