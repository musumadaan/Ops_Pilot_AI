"""Build the dashboard:  python -m modules.bi.build --out dashboard.html"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from modules.analytics.db import get_engine

from .data import build_dataset
from .render import render


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="modules/bi/dashboard.html")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--ml-report", default="modules/ml/reports/ml_report.json")
    a = ap.parse_args()
    ml = json.loads(Path(a.ml_report).read_text()) if Path(a.ml_report).exists() else None
    ds = build_dataset(get_engine(), a.start, a.end, ml_report=ml)
    Path(a.out).write_text(render(ds), encoding="utf-8")
    print(f"wrote {a.out}: {len(ds['drill'])} flagged invoices, {len(ds['kpis'])} KPIs")


if __name__ == "__main__":
    main()
