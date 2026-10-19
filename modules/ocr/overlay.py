"""Draw extracted-field boxes on the page image, to check bounding boxes by eye.

This is the data behind the UI's trust feature: click a field, see where it came from.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .engines.pdf_text import render_pdf
from .extract.invoice_schema import BBox, InvoiceExtraction

COLOURS = {"label": (60, 160, 60), "heuristic": (0, 140, 255), "derived": (200, 120, 0), "human": (200, 60, 160)}
FIELD_NAMES = ("invoice_number", "invoice_date", "due_date", "po_number", "supplier_name",
               "currency", "subtotal", "tax", "total")


def _box(img: np.ndarray, b: BBox, colour: tuple[int, int, int], label: str) -> None:
    h, w = img.shape[:2]
    p0, p1 = (int(b.x0 * w), int(b.y0 * h)), (int(b.x1 * w), int(b.y1 * h))
    cv2.rectangle(img, p0, p1, colour, 2)
    cv2.putText(img, label, (p0[0], max(12, p0[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)


def draw_overlay(inv: InvoiceExtraction, page_images: list[np.ndarray]) -> list[np.ndarray]:
    out = [im.copy() if im.ndim == 3 else cv2.cvtColor(im, cv2.COLOR_GRAY2BGR) for im in page_images]
    for name in FIELD_NAMES:
        f = getattr(inv, name)
        if f is not None and f.bbox is not None and f.bbox.page <= len(out):
            _box(out[f.bbox.page - 1], f.bbox, COLOURS.get(f.source, (0, 0, 255)), name)
    for item in inv.line_items:
        if item.bbox is not None and item.bbox.page <= len(out):
            _box(out[item.bbox.page - 1], item.bbox, (160, 160, 160), f"line {item.line_no}")
    return out


def overlay_file(inv: InvoiceExtraction, source: str | Path, out_path: str | Path, dpi: int = 110) -> Path:
    source = Path(source)
    imgs = render_pdf(source, dpi) if source.suffix.lower() == ".pdf" else [cv2.imread(str(source))]
    pages = draw_overlay(inv, imgs)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), np.vstack(pages) if len(pages) > 1 else pages[0])
    return out_path
