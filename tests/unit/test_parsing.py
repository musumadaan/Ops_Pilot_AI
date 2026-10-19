from datetime import date
from decimal import Decimal

import pytest

from modules.ocr.extract.parsing import (
    clean_id,
    detect_currency,
    is_money_token,
    parse_date,
    parse_money,
)


@pytest.mark.parametrize("text,expected", [
    ("$1,234.56", Decimal("1234.56")),
    ("1.234,56", Decimal("1234.56")),
    ("1 234,50", Decimal("1234.50")),
    ("(45.00)", Decimal("-45.00")),
    ("-12.30", Decimal("-12.30")),
    ("C$99.99", Decimal("99.99")),
    ("0.05", Decimal("0.05")),
    ("1,234", Decimal(1234)),
])
def test_parse_money(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "--", "N/A"])
def test_parse_money_rejects_non_numeric(text):
    assert parse_money(text) is None


def test_parse_money_returns_decimal_not_float():
    assert isinstance(parse_money("10.10"), Decimal)


@pytest.mark.parametrize("tok,ok", [
    ("1,234.56", True), ("$45.00", True), ("(12.50)", True),
    ("15", False), ("INV-2026", False), ("12.5", False),
])
def test_is_money_token(tok, ok):
    assert is_money_token(tok) is ok


@pytest.mark.parametrize("text,expected,ambiguous", [
    ("2026-06-11", date(2026, 6, 11), False),
    ("11 Jun 2026", date(2026, 6, 11), False),
    ("June 11, 2026", date(2026, 6, 11), False),
    ("13/04/2026", date(2026, 4, 13), False),      # day > 12 is unambiguous
    ("04/13/2026", date(2026, 4, 13), False),
])
def test_parse_date_unambiguous(text, expected, ambiguous):
    assert parse_date(text) == (expected, ambiguous)


def test_parse_date_flags_ambiguous_numeric():
    d, amb = parse_date("03/04/2026")
    assert amb is True and d == date(2026, 3, 4)            # month-first default
    d2, amb2 = parse_date("03/04/2026", day_first=True)
    assert amb2 is True and d2 == date(2026, 4, 3)


def test_parse_date_invalid():
    assert parse_date("not a date") == (None, False)
    assert parse_date("2026-02-31")[0] is None


def test_detect_currency():
    assert detect_currency("Currency: CAD") == "CAD"
    assert detect_currency("Total € 10") == "EUR"
    assert detect_currency("Total $10") is None            # bare $ is ambiguous on purpose


def test_clean_id():
    assert clean_id(": INV-001,") == "INV-001"
