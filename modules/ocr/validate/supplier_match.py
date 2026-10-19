"""Fuzzy supplier matching against a supplier master (rapidfuzz).

Thresholds: >= ``auto`` is matched; between ``review`` and ``auto`` is ambiguous (human review);
below ``review`` is unknown. Names are normalised (case, punctuation, legal suffixes) first.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, process

from ..extract.invoice_schema import SupplierMatch, ValidationFlag

_SUFFIXES = re.compile(
    r"\b(inc|incorporated|ltd|limited|llc|corp|corporation|co|company|pvt|plc|gmbh|ulc)\b\.?", re.IGNORECASE)


def normalize_name(name: str) -> str:
    s = _SUFFIXES.sub(" ", name.lower())
    s = re.sub(r"[^a-z0-9 ]+", " ", s.replace("&", " and "))
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class SupplierMaster:
    ids: list[str]
    names: list[str]

    @classmethod
    def from_csv(cls, path: str | Path, id_col: str = "supplier_id", name_col: str = "name") -> SupplierMaster:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        return cls([r[id_col] for r in rows], [r[name_col] for r in rows])

    def __post_init__(self) -> None:
        self._norm = [normalize_name(n) for n in self.names]

    def match(self, name: str, auto: float = 92.0, review: float = 75.0) -> SupplierMatch:
        if not self.names:
            return SupplierMatch(supplier_id=None, master_name=None, score=0.0, status="unknown")
        hit = process.extractOne(normalize_name(name), self._norm, scorer=fuzz.token_sort_ratio)
        _, score, idx = hit
        status = "matched" if score >= auto else "ambiguous" if score >= review else "unknown"
        if status == "unknown":
            return SupplierMatch(supplier_id=None, master_name=None, score=float(score), status=status)
        return SupplierMatch(supplier_id=self.ids[idx], master_name=self.names[idx],
                             score=float(score), status=status)


def supplier_flags(match: SupplierMatch | None) -> list[ValidationFlag]:
    if match is None:
        return []
    if match.status == "unknown":
        return [ValidationFlag(code="supplier_unknown", severity="error", field="supplier_name",
                               message=f"No supplier-master match (best score {match.score:.0f}).")]
    if match.status == "ambiguous":
        return [ValidationFlag(code="supplier_ambiguous", severity="warning", field="supplier_name",
                               message=f"Closest master record '{match.master_name}' scored "
                                       f"{match.score:.0f}; confirm before posting.")]
    return []
