"""Digital-PDF path: read the text layer with PyMuPDF and render pages for the OCR path.

Rule: if a PDF already has selectable text, skip OCR. Only render and OCR when the text layer
is missing or too thin (scans, image-only PDFs).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pymupdf

from .base import OcrPage, OcrWord

MIN_WORDS_PER_PAGE = 15  # below this we treat the page as "no usable text layer"


def extract_text_layer(path: str | Path) -> list[OcrPage] | None:
    """Return pages from the PDF text layer, or None if the layer is missing/too thin."""
    pages: list[OcrPage] = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            rect = page.rect
            words = [
                OcrWord(text=w[4], conf=1.0, x0=w[0], y0=w[1], x1=w[2], y1=w[3])
                for w in page.get_text("words")
                if w[4].strip()
            ]
            pages.append(OcrPage(i, rect.width, rect.height, words, source="pdf_text"))
    if not pages or any(len(p.words) < MIN_WORDS_PER_PAGE for p in pages):
        return None
    return pages


def render_pdf(path: str | Path, dpi: int = 300) -> list[np.ndarray]:
    """Render each page to a BGR uint8 array."""
    images: list[np.ndarray] = []
    with pymupdf.open(path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            images.append(arr[:, :, ::-1].copy())  # RGB -> BGR
    return images
