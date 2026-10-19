"""A deliberately small, *safe* subset of OData query options: $filter, $orderby, $top, $skip, $select.

Safe means: columns are checked against a whitelist and values are bound as parameters, so no query text from the
client ever reaches SQL. Unsupported syntax is rejected with ``ODataError`` (HTTP 400), never silently ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

OPS = {"eq": "=", "ne": "<>", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}
_TOKEN = re.compile(r"""\s*(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<op>eq|ne|gt|ge|lt|le)\s+(?P<val>'(?:[^']|'')*'|-?\d+(?:\.\d+)?)\s*""")
MAX_TOP = 200
DEFAULT_TOP = 50


class ODataError(ValueError):
    pass


@dataclass
class Query:
    where: str
    params: list
    order: str
    top: int
    skip: int
    select: list[str]


def parse_filter(expr: str | None, allowed: set[str]) -> tuple[str, list]:
    if not expr:
        return "1=1", []
    pos, clauses, params = 0, [], []
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m:
            raise ODataError(f"cannot parse $filter near: {expr[pos:pos + 30]!r}")
        f = m["field"]
        if f not in allowed:
            raise ODataError(f"unknown field '{f}' in $filter")
        v = m["val"]
        params.append(v[1:-1].replace("''", "'") if v.startswith("'") else (float(v) if "." in v else int(v)))
        clauses.append(f"{f} {OPS[m['op']]} ?")
        pos = m.end()
        if pos >= len(expr):
            break
        j = re.match(r"\s*and\s+", expr[pos:], flags=re.IGNORECASE)
        if not j:
            raise ODataError("only 'and' is supported between $filter clauses")
        pos += j.end()
    return " AND ".join(clauses), params


def build(allowed: set[str], filter: str | None, orderby: str | None, top: int | None, skip: int | None,
          select: str | None, default_order: str) -> Query:
    where, params = parse_filter(filter, allowed)
    order = default_order
    if orderby:
        parts = []
        for p in orderby.split(","):
            bits = p.strip().split()
            if not bits or bits[0] not in allowed or (len(bits) == 2 and bits[1].lower() not in ("asc", "desc")) or len(bits) > 2:
                raise ODataError(f"bad $orderby term: {p.strip()!r}")
            parts.append(f"{bits[0]} {bits[1].upper() if len(bits) == 2 else 'ASC'}")
        order = ", ".join(parts + ([default_order] if default_order.split()[0] not in [p.split()[0] for p in parts] else []))
    top = DEFAULT_TOP if top is None else top
    skip = skip or 0
    if not 0 <= top <= MAX_TOP or skip < 0:
        raise ODataError(f"$top must be 0..{MAX_TOP} and $skip >= 0")
    cols = [c.strip() for c in select.split(",")] if select else []
    bad = [c for c in cols if c not in allowed]
    if bad:
        raise ODataError(f"unknown field(s) in $select: {bad}")
    return Query(where, params, order, top, skip, cols)
