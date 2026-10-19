"""Group OCR words into visual lines and compute normalised boxes."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from ..engines.base import OcrPage, OcrWord
from .invoice_schema import BBox


@dataclass
class Line:
    page: int
    page_w: float
    page_h: float
    words: list[OcrWord]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def yc(self) -> float:
        return sum(w.yc for w in self.words) / len(self.words)

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    def spans(self) -> list[tuple[int, int]]:
        """Character span of each word inside ``self.text``."""
        out, pos = [], 0
        for w in self.words:
            out.append((pos, pos + len(w.text)))
            pos += len(w.text) + 1
        return out

    def bbox_of(self, words: list[OcrWord]) -> BBox:
        return union_bbox(self.page, self.page_w, self.page_h, words)


def union_bbox(page: int, pw: float, ph: float, words: list[OcrWord]) -> BBox:
    x0 = min(w.x0 for w in words) / pw
    y0 = min(w.y0 for w in words) / ph
    x1 = max(w.x1 for w in words) / pw
    y1 = max(w.y1 for w in words) / ph
    c = lambda v: max(0.0, min(1.0, v))
    return BBox(page=page, x0=c(x0), y0=c(y0), x1=c(x1), y1=c(y1))


def group_lines(page: OcrPage, y_tol: float = 0.6) -> list[Line]:
    """Cluster words into lines by vertical centre; tolerance is relative to median height."""
    if not page.words:
        return []
    med_h = median(w.h for w in page.words)
    words = sorted(page.words, key=lambda w: (w.yc, w.x0))
    lines: list[list[OcrWord]] = []
    centres: list[float] = []
    for w in words:
        if lines and abs(w.yc - centres[-1]) <= y_tol * med_h:
            lines[-1].append(w)
            centres[-1] = sum(x.yc for x in lines[-1]) / len(lines[-1])
        else:
            lines.append([w])
            centres.append(w.yc)
    return [
        Line(page.page, page.width, page.height, sorted(ws, key=lambda w: w.x0)) for ws in lines
    ]
