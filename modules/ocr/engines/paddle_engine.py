"""PaddleOCR engine (optional challenger). CPU build on macOS.

Install with ``uv add paddleocr paddlepaddle``. Imported lazily so the rest of the package
works without it. Handles both the 3.x (``predict``) and 2.x (``ocr``) result formats.
Paddle returns text *lines*; they are split into words with approximate boxes.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .base import OcrPage, OcrWord, split_line_to_words


class PaddleEngine:
    name = "paddleocr"

    def __init__(self, lang: str = "en") -> None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("PaddleOCR not installed: uv add paddleocr paddlepaddle") from exc
        try:  # 3.x API
            self._ocr = PaddleOCR(
                lang=lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            self._api = 3
        except TypeError:  # 2.x API
            self._ocr = PaddleOCR(lang=lang, use_angle_cls=False, show_log=False)
            self._api = 2

    def _lines(self, img: np.ndarray) -> list[tuple[str, float, float, float, float, float]]:
        out: list[tuple[str, float, float, float, float, float]] = []
        if self._api == 3:
            for res in self._ocr.predict(img):
                data = res.json["res"] if hasattr(res, "json") else res
                texts = data.get("rec_texts", [])
                scores = data.get("rec_scores", [])
                boxes = data.get("rec_boxes")
                if boxes is None:
                    polys = data.get("rec_polys", data.get("dt_polys", []))
                    boxes = [
                        [min(p[0] for p in poly), min(p[1] for p in poly),
                         max(p[0] for p in poly), max(p[1] for p in poly)]
                        for poly in polys
                    ]
                for t, s, b in zip(texts, scores, boxes):
                    out.append((t, float(s), float(b[0]), float(b[1]), float(b[2]), float(b[3])))
        else:
            result = self._ocr.ocr(img, cls=False) or [[]]
            for poly, (t, s) in result[0] or []:
                xs, ys = [p[0] for p in poly], [p[1] for p in poly]
                out.append((t, float(s), min(xs), min(ys), max(xs), max(ys)))
        return out

    def run(self, images: Sequence[np.ndarray]) -> list[OcrPage]:
        pages: list[OcrPage] = []
        for i, img in enumerate(images, start=1):
            h, w = img.shape[:2]
            words: list[OcrWord] = []
            for text, conf, x0, y0, x1, y1 in self._lines(img):
                words.extend(split_line_to_words(text, conf, x0, y0, x1, y1))
            pages.append(OcrPage(i, float(w), float(h), words, source=self.name))
        return pages
