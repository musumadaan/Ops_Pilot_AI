"""Apple Vision OCR via ``ocrmac`` (macOS only). Use as a local comparison baseline.

Not CI-portable: it will not run on Linux runners, so keep Tesseract/Paddle as the portable
engines. Install with ``uv add ocrmac`` on the Mac.
"""
from __future__ import annotations

import sys
from collections.abc import Sequence

import cv2
import numpy as np
from PIL import Image

from .base import OcrPage, OcrWord, split_line_to_words


class VisionEngine:
    name = "apple_vision"

    def __init__(self, recognition_level: str = "accurate") -> None:
        if sys.platform != "darwin":
            raise RuntimeError("Apple Vision OCR is only available on macOS")
        try:
            from ocrmac import ocrmac
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("ocrmac not installed: uv add ocrmac") from exc
        self._ocrmac = ocrmac
        self.level = recognition_level

    def run(self, images: Sequence[np.ndarray]) -> list[OcrPage]:  # pragma: no cover - macOS
        pages: list[OcrPage] = []
        for i, img in enumerate(images, start=1):
            h, w = img.shape[:2]
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if img.ndim == 3 else img
            annotations = self._ocrmac.OCR(
                Image.fromarray(rgb), recognition_level=self.level
            ).recognize()
            words: list[OcrWord] = []
            for text, conf, (bx, by, bw, bh) in annotations:
                # Vision boxes are normalised with the origin at the bottom-left.
                x0, x1 = bx * w, (bx + bw) * w
                y0, y1 = (1.0 - (by + bh)) * h, (1.0 - by) * h
                words.extend(split_line_to_words(text, float(conf), x0, y0, x1, y1))
            pages.append(OcrPage(i, float(w), float(h), words, source=self.name))
        return pages
