"""Parsing helpers for money, dates and identifiers. Pure functions, heavily unit-tested."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

_CUR_PREFIX = re.compile(r"^(?:C\$|US\$|CA\$|CAD|USD|EUR|GBP|[$€£])+", re.IGNORECASE)

# A token that "looks like" a money amount with exactly 2 decimals.
MONEY_TOKEN = re.compile(
    r"^[-(]?(?:C\$|US\$|CA\$|[$€£])?\d{1,3}(?:[,. ]\d{3})*[.,]\d{2}[)-]?$"
    r"|^[-(]?(?:C\$|US\$|CA\$|[$€£])?\d+[.,]\d{2}[)-]?$"
)
QTY_TOKEN = re.compile(r"^\d+(?:\.\d+)?$")
ISO_CCY = ("CAD", "USD", "EUR", "GBP", "AUD", "NZD", "CHF", "JPY", "INR", "MXN")


def is_money_token(tok: str) -> bool:
    return bool(MONEY_TOKEN.match(tok.strip()))


def _fix_ocr_digits(tok: str) -> str:
    """Repair classic OCR confusions, only when the token is mostly digits already."""
    digits = sum(c.isdigit() for c in tok)
    if digits < max(2, len(tok) // 2):
        return tok
    return tok.translate(str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"}))


def parse_money(text: str) -> Decimal | None:
    """Parse '$1,234.56', '1.234,56', '(45.00)', '1 234,50' to Decimal; None if not numeric."""
    s = text.strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-") or s.endswith("-")
    s = _CUR_PREFIX.sub("", s.strip("()- "))
    s = _fix_ocr_digits(s)
    s = re.sub(r"[^\d.,]", "", s)
    if not re.search(r"\d", s):
        return None
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s:
        s = s.replace(",", ".") if (s.count(",") == 1 and re.search(r",\d{2}$", s)) else s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        val = Decimal(s)
    except InvalidOperation:
        return None
    return -val if neg else val


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_ISO = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b")
_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(text: str, day_first: bool = False) -> tuple[date | None, bool]:
    """Return (date, ambiguous). ``ambiguous`` is True for 03/04/2026-style values where both
    day-first and month-first readings are valid and different. ``day_first`` sets the default
    reading for such values (Canada: month-first is common but not universal, so we flag it).
    """
    if m := _ISO.search(text):
        return _mk(int(m[1]), int(m[2]), int(m[3])), False
    if m := _MDY.search(text):
        mon = _MONTHS.get(m[1][:3].lower())
        if mon:
            return _mk(int(m[3]), mon, int(m[2])), False
    if m := _DMY.search(text):
        mon = _MONTHS.get(m[2][:3].lower())
        if mon:
            return _mk(int(m[3]), mon, int(m[1])), False
    if m := _NUMERIC.search(text):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if a > 12:
            return _mk(y, b, a), False
        if b > 12:
            return _mk(y, a, b), False
        ambiguous = a != b
        return (_mk(y, b, a) if day_first else _mk(y, a, b)), ambiguous
    return None, False


def detect_currency(text: str) -> str | None:
    """Explicit ISO code wins. A bare '$' is ambiguous (CAD/USD/AUD...) and returns None."""
    for code in ISO_CCY:
        if re.search(rf"\b{code}\b", text):
            return code
    if "€" in text:
        return "EUR"
    if "£" in text:
        return "GBP"
    return None


ID_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_/\.]{2,}")


def clean_id(tok: str) -> str:
    """Strip label punctuation from the start/end of an identifier token."""
    return tok.strip(" :#.,;|")
