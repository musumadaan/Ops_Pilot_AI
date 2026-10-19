"""Tesseract baseline engine (CPU, CI-portable). Needs the ``tesseract`` binary on PATH."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytesseract
from pytesseract import Output

from .base import OcrPage, OcrWord


class TesseractEngine:
    def __init__(self, lang: str = "eng", psm: int = 6, oem: int = 1) -> None:
        self.lang, self.psm, self.oem = lang, psm, oem
        self.name = f"tesseract_psm{psm}"

    def run(self, images: Sequence[np.ndarray]) -> list[OcrPage]:
        config = f"--oem {self.oem} --psm {self.psm}"
        pages: list[OcrPage] = []
        for i, img in enumerate(images, start=1):
            h, w = img.shape[:2]
            data = pytesseract.image_to_data(
                img, lang=self.lang, config=config, output_type=Output.DICT
            )
            words: list[OcrWord] = []
            for text, conf, x, y, bw, bh in zip(
                data["text"], data["conf"], data["left"], data["top"],
                data["width"], data["height"],
            ):
                text = (text or "").strip()
                c = float(conf)
                if not text or c < 0:  # -1 marks non-word rows (blocks/lines)
                    continue
                words.append(OcrWord(text, c / 100.0, x, y, x + bw, y + bh))
            pages.append(OcrPage(i, float(w), float(h), words, source=self.name))
        return pages
