"""Rule + layout extractor: OCR words -> invoice fields and line items.

Strategy (deterministic first; an LLM/VLM fallback can be added behind the same interface):
1. Group words into lines.
2. Find label patterns ("Invoice No", "Total Due", ...) and read the value to the right of the
   label, or just below it when the right side is empty.
3. Locate the line-item table from its header row, parse rows from the right (amount, unit
   price, quantity), and stop at the first footer line (subtotal / tax / total).
Every value keeps confidence, page and a normalised bounding box.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from statistics import mean

from ..engines.base import OcrPage, OcrWord
from .invoice_schema import (
    BBox,
    DateField,
    FieldMeta,
    LineItem,
    MoneyField,
    TextField,
    ValidationFlag,
)
from .layout import Line, group_lines, union_bbox
from .parsing import (
    ID_TOKEN,
    ISO_CCY,
    QTY_TOKEN,
    clean_id,
    is_money_token,
    parse_date,
    parse_money,
)

I = re.IGNORECASE

# ------------------------------------------------------------------ label patterns
P_INVOICE_NO = [re.compile(r"\binvoice\s*(?:number|num|no|id)\b\.?|\binvoice\s*#|\binv\s*(?:no\b\.?|#)", I)]
P_PO = [re.compile(r"\b(?:purchase\s+order(?:\s*(?:number|num|no\b\.?|#))?|p\.?\s?o\.?\s*(?:number|num|no\b\.?|#))", I)]
P_INV_DATE = [re.compile(r"\binvoice\s*date\b|\bdate\s+of\s+issue\b|\bissue\s*date\b|\bissued\b", I),
              re.compile(r"^\s*date\b", I)]
P_DUE = [re.compile(r"\b(?:payment\s+due(?:\s*date)?|due\s*date|due)\b", I)]
P_SUBTOTAL = re.compile(r"sub\s*-?\s*total", I)
P_TOTAL_STRONG = re.compile(
    r"total\s+due|amount\s+due|balance\s+due|grand\s+total|total\s+amount|invoice\s+total"
    r"|total\s+payable|amount\s+payable", I)
P_TOTAL_PLAIN = re.compile(r"(?<![A-Za-z\-])(?<!sub )total\b", I)
P_TAX = re.compile(r"\b(?:tax|gst|hst|vat|pst|qst)\b", I)
P_TAX_EXCLUDE = re.compile(r"\b(?:id|no|number|reg|registration)\b|#", I)
P_SUPPLIER_LABEL = re.compile(r"\b(?:from|supplier|vendor|seller|issued\s+by|remit\s+to)\b\s*:?", I)
P_FOOTER = re.compile(
    r"^\W*(?:sub\s*-?\s*total|total|tax|gst|hst|vat|pst|qst|amount\s+due|balance|grand\s+total"
    r"|shipping|discount|freight)\b", I)
P_NOISE = re.compile(
    r"invoice|bill\s*to|ship\s*to|\bdate\b|page\s*\d|www\.|@|phone|\btel\b|\bfax\b|\bgst\b|\bhst\b"
    r"|\bvat\b|po\s*box|\bremit\b|statement|\bdue\b", I)
P_SKU = re.compile(r"^[A-Z]{2,6}-?\d{2,}[A-Z0-9\-]*$")
UNIT_WORDS = {"ea", "pcs", "pc", "hr", "hrs", "kg", "m", "box", "set", "lot", "unit", "units"}


@dataclass
class Extracted:
    fields: dict[str, FieldMeta] = field(default_factory=dict)
    line_items: list[LineItem] = field(default_factory=list)
    flags: list[ValidationFlag] = field(default_factory=list)
    mean_conf: float | None = None


# ------------------------------------------------------------------ small helpers
def _conf(words: list[OcrWord]) -> float:
    return float(mean(w.conf for w in words)) if words else 0.0


def _meta(line: Line, words: list[OcrWord], source: str = "label") -> dict:
    return dict(
        raw=" ".join(w.text for w in words),
        confidence=max(0.0, min(1.0, _conf(words))),
        page=line.page,
        bbox=line.bbox_of(words),
        source=source,
    )


def _words_after(line: Line, end: int) -> list[OcrWord]:
    out: list[OcrWord] = []
    for w, (s, e) in zip(line.words, line.spans()):
        if s >= end:
            out.append(w)
        elif s < end < e:  # label ended mid-word, e.g. "No:" -> keep ":"-less remainder
            rest = w.text[end - s:]
            if rest.strip(" :#.-|"):
                out.append(OcrWord(rest, w.conf, w.x0, w.y0, w.x1, w.y1))
    while out and not out[0].text.strip(" :#.-|"):
        out.pop(0)
    return out


def _words_below(lines: list[Line], idx: int, label_words: list[OcrWord]) -> tuple[Line, list[OcrWord]] | None:
    if idx + 1 >= len(lines) or lines[idx + 1].page != lines[idx].page or not label_words:
        return None
    nxt, pw = lines[idx + 1], lines[idx].page_w
    ax0, ax1 = min(w.x0 for w in label_words), max(w.x1 for w in label_words)
    cand = [w for w in nxt.words if ax0 - 0.03 * pw <= w.x0 <= ax1 + 0.25 * pw]
    out: list[OcrWord] = []
    for w in cand:
        if out and w.x0 - out[-1].x1 > 0.05 * pw:
            break
        out.append(w)
    return (nxt, out) if out else None


def _label_candidates(
    lines: list[Line], patterns: list[re.Pattern], skip: re.Pattern | None = None
):
    """Yield (line, value_words) for every label hit, same-line value first then below-line."""
    for pat in patterns:
        for i, line in enumerate(lines):
            text = line.text
            if skip and skip.search(text):
                continue
            m = pat.search(text)
            if not m:
                continue
            val = _words_after(line, m.end())
            if val:
                yield line, val
            else:
                label_words = [w for w, (s, e) in zip(line.words, line.spans()) if e > m.start() and s < m.end()]
                below = _words_below(lines, i, label_words)
                if below:
                    yield below


def _split_groups(line: Line, gap_factor: float = 2.5) -> list[list[OcrWord]]:
    """Split a line into word groups wherever the horizontal gap is large (column breaks)."""
    groups: list[list[OcrWord]] = [[line.words[0]]]
    for prev, w in zip(line.words, line.words[1:]):
        if w.x0 - prev.x1 > gap_factor * max(prev.h, w.h):
            groups.append([w])
        else:
            groups[-1].append(w)
    return groups


# ------------------------------------------------------------------ the extractor
class FieldExtractor:
    def __init__(self, day_first: bool = False) -> None:
        self.day_first = day_first

    # ---- typed readers
    def _text_id(self, lines, patterns) -> TextField | None:
        for line, val in _label_candidates(lines, patterns):
            for w in val[:3]:
                tok = clean_id(w.text)
                if ID_TOKEN.fullmatch(tok) and any(c.isdigit() for c in tok):
                    return TextField(value=tok, **_meta(line, [w]))
        return None

    def _date(self, lines, patterns, skip=None) -> DateField | None:
        for line, val in _label_candidates(lines, patterns, skip):
            for n in range(1, min(5, len(val)) + 1):
                used = val[:n]
                d, amb = parse_date(" ".join(w.text for w in used), self.day_first)
                if d:
                    return DateField(value=d, ambiguous=amb, **_meta(line, used))
        return None

    @staticmethod
    def _last_money(line: Line) -> OcrWord | None:
        money = [w for w in line.words if is_money_token(w.text)]
        return money[-1] if money else None

    def _money_from(self, line: Line, w: OcrWord) -> MoneyField | None:
        val = parse_money(w.text)
        return MoneyField(value=val, **_meta(line, [w])) if val is not None else None

    def _total(self, lines: list[Line]) -> MoneyField | None:
        best: tuple[int, int, MoneyField] | None = None
        for i, line in enumerate(lines):
            text = line.text
            if P_SUBTOTAL.search(text):
                continue
            prio = 3 if P_TOTAL_STRONG.search(text) else 1 if P_TOTAL_PLAIN.search(text) else 0
            if not prio:
                continue
            w = self._last_money(line)
            mf = self._money_from(line, w) if w else None
            if mf and (best is None or (prio, i) >= (best[0], best[1])):
                best = (prio, i, mf)
        return best[2] if best else None

    def _subtotal(self, lines: list[Line]) -> MoneyField | None:
        hit = None
        for line in lines:
            if P_SUBTOTAL.search(line.text) and (w := self._last_money(line)):
                hit = self._money_from(line, w) or hit
        return hit

    def _tax(self, lines: list[Line], start: int) -> MoneyField | None:
        parts: list[tuple[Line, OcrWord, Decimal]] = []
        for line in lines[start:]:
            text = line.text
            if P_SUBTOTAL.search(text) or P_TOTAL_STRONG.search(text) or P_TOTAL_PLAIN.search(text):
                continue
            if not P_TAX.search(text) or P_TAX_EXCLUDE.search(text):
                continue
            w = self._last_money(line)
            if w and (v := parse_money(w.text)) is not None:
                parts.append((line, w, v))
        if not parts:
            return None
        total = sum((v for _, _, v in parts), Decimal(0))
        line, w, _ = parts[-1]
        words = [p[1] for p in parts]
        meta = _meta(line, words, "label")
        meta["raw"] = " + ".join(p[1].text for p in parts)
        meta["page"] = line.page
        meta["bbox"] = BBox(**union_bbox(line.page, line.page_w, line.page_h, words).model_dump())
        return MoneyField(value=total, **meta)

    def _supplier(self, lines: list[Line]) -> TextField | None:
        for i, line in enumerate(lines):
            if m := P_SUPPLIER_LABEL.search(line.text):
                val = _words_after(line, m.end())
                src_line = line
                if not val and i + 1 < len(lines):
                    src_line, val = lines[i + 1], lines[i + 1].words
                if val:
                    group = _split_groups(src_line)[0] if val is src_line.words else _first_group(val)
                    return TextField(value=" ".join(w.text for w in group), **_meta(src_line, group))
        first_page = [ln for ln in lines if ln.page == lines[0].page] if lines else []
        limit = first_page[0].page_h * 0.30 if first_page else 0
        for line in first_page:
            if line.yc > limit:
                break
            for group in _split_groups(line):
                text = " ".join(w.text for w in group)
                letters = sum(c.isalpha() for c in text)
                if letters >= 3 and letters / max(len(text), 1) >= 0.6 and not P_NOISE.search(text):
                    return TextField(value=text, **_meta(line, group, "heuristic"))
        return None

    def _currency(self, lines: list[Line]) -> tuple[TextField | None, list[ValidationFlag]]:
        flags: list[ValidationFlag] = []
        full = "\n".join(l.text for l in lines)
        found = [(m.start(), m[0]) for code in ISO_CCY for m in re.finditer(rf"\b{code}\b", full)]
        symbols = [(full.find(s), c) for s, c in (("€", "EUR"), ("£", "GBP")) if s in full]
        found += symbols
        if not found:
            if "$" in full:
                flags.append(ValidationFlag(code="currency_ambiguous", severity="warning",
                                            field="currency",
                                            message="Only a '$' symbol was found; currency not explicit."))
            return None, flags
        codes = {c for _, c in found}
        first = min(found)[1]
        if len(codes) > 1:
            flags.append(ValidationFlag(code="currency_conflict", severity="warning", field="currency",
                                        message=f"Multiple currencies found: {sorted(codes)}."))
        for line in lines:  # attach a bbox to the first line that mentions it
            for w in line.words:
                if first in w.text:
                    return TextField(value=first, **_meta(line, [w])), flags
        return TextField(value=first, raw=first, confidence=0.5, source="derived"), flags

    # ---- line items
    @staticmethod
    def _is_header(text: str) -> bool:
        t = text.lower()
        return (("description" in t or "item" in t) and ("qty" in t or "quantity" in t)
                and any(k in t for k in ("price", "rate", "amount", "total")))

    def _line_items(self, lines: list[Line]) -> tuple[list[LineItem], int]:
        """Return (items, index of first footer line or len(lines))."""
        items: list[LineItem] = []
        in_table, last_row_line = False, None
        for i, line in enumerate(lines):
            text = line.text
            if not in_table:
                in_table = self._is_header(text)
                continue
            if P_FOOTER.search(text) and self._last_money(line):
                return items, i
            row = self._parse_row(line, len(items) + 1)
            if row:
                items.append(row)
                last_row_line = line
            elif items and last_row_line is not None and not self._last_money(line) \
                    and line.page == last_row_line.page and not P_FOOTER.search(text):
                items[-1].description = f"{items[-1].description} {text}".strip()
        return items, len(lines)

    def _parse_row(self, line: Line, no: int) -> LineItem | None:
        toks = line.words
        if len(toks) < 2 or not is_money_token(toks[-1].text):
            return None
        used = [toks[-1]]
        amount = parse_money(toks[-1].text)
        j = len(toks) - 2
        unit_price = quantity = None
        if j >= 0 and is_money_token(toks[j].text):
            unit_price = parse_money(toks[j].text)
            used.append(toks[j])
            j -= 1
        if j >= 0 and toks[j].text.lower().strip(".") in UNIT_WORDS and j >= 1 and QTY_TOKEN.match(toks[j - 1].text):
            used.append(toks[j])
            j -= 1
        if j >= 0 and QTY_TOKEN.match(toks[j].text):
            quantity = Decimal(toks[j].text)
            used.append(toks[j])
            j -= 1
        if quantity is None and unit_price is None:
            return None  # a lone number is not enough to call it a row
        left = toks[: j + 1]
        sku = None
        if left and P_SKU.match(left[0].text) and len(left) > 1:
            sku, left = left[0].text, left[1:]
        elif left and re.fullmatch(r"\d{1,3}", left[0].text) and len(left) > 1:
            left = left[1:]  # row number column
        desc = " ".join(w.text for w in left).strip()
        if not desc:
            return None
        return LineItem(line_no=no, description=desc, sku=sku, quantity=quantity,
                        unit_price=unit_price, amount=amount, confidence=min(w.conf for w in toks),
                        page=line.page, bbox=line.bbox_of(toks))

    # ---- public API
    def extract(self, pages: list[OcrPage]) -> Extracted:
        lines: list[Line] = [ln for p in pages for ln in group_lines(p)]
        out = Extracted()
        if not lines:
            return out
        out.mean_conf = float(mean(w.conf for p in pages for w in p.words)) if any(p.words for p in pages) else None
        items, footer_start = self._line_items(lines)
        out.line_items = items

        f = out.fields
        for name, val in (
            ("invoice_number", self._text_id(lines, P_INVOICE_NO)),
            ("po_number", self._text_id(lines, P_PO)),
            ("invoice_date", self._date(lines, P_INV_DATE, skip=re.compile(r"\bdue\b", I))),
            ("due_date", self._date(lines, P_DUE, skip=re.compile(r"total|amount|balance", I))),
            ("supplier_name", self._supplier(lines)),
            ("subtotal", self._subtotal(lines)),
            ("tax", self._tax(lines, footer_start if items else 0)),
            ("total", self._total(lines)),
        ):
            if val is not None:
                f[name] = val
        cur, cflags = self._currency(lines)
        if cur:
            f["currency"] = cur
        out.flags.extend(cflags)
        for name in ("invoice_date", "due_date"):
            d = f.get(name)
            if isinstance(d, DateField) and d.ambiguous:
                out.flags.append(ValidationFlag(
                    code="date_ambiguous", severity="warning", field=name,
                    message=f"'{d.raw}' can be read day-first or month-first; assumed "
                            f"{'day' if self.day_first else 'month'}-first."))
        return out


def _first_group(words: list[OcrWord], gap_factor: float = 2.5) -> list[OcrWord]:
    group = [words[0]]
    for prev, w in zip(words, words[1:]):
        if w.x0 - prev.x1 > gap_factor * max(prev.h, w.h):
            break
        group.append(w)
    return group
