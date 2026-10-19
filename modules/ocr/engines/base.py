"""OCR engine interface. Every engine returns the same ``OcrPage`` structure so engines are
swappable and benchmarkable. Coordinates are in page units (pixels for images, points for
PDF text) with origin at the top-left; normalisation happens later using page width/height.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


@dataclass(slots=True)
class OcrWord:
    text: str
    conf: float  # 0..1
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return max(self.y1 - self.y0, 1e-6)


@dataclass
class OcrPage:
    page: int  # 1-based
    width: float
    height: float
    words: list[OcrWord] = field(default_factory=list)
    source: str = ""


class OcrEngine(Protocol):
    name: str

    def run(self, images: Sequence[np.ndarray]) -> list[OcrPage]:
        """OCR one image per page (BGR or grayscale uint8) and return one OcrPage each."""
        ...


def split_line_to_words(
    text: str, conf: float, x0: float, y0: float, x1: float, y1: float
) -> list[OcrWord]:
    """Split a line-level OCR result into words, sharing the box width by character count.

    Engines such as PaddleOCR and Apple Vision return whole text lines, not words. Splitting
    keeps the downstream layout code uniform. Box positions are approximate by design.
    """
    tokens = text.split()
    if not tokens:
        return []
    total_chars = sum(len(t) for t in tokens) + (len(tokens) - 1)
    width = x1 - x0
    out: list[OcrWord] = []
    cursor = x0
    for i, tok in enumerate(tokens):
        w = width * len(tok) / total_chars
        out.append(OcrWord(tok, conf, cursor, y0, cursor + w, y1))
        cursor += w + (width / total_chars if i < len(tokens) - 1 else 0)
    return out
