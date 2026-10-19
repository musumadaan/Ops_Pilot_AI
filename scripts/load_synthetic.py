"""Validate (Pandera) and load the synthetic CSVs into PostgreSQL.

    DATABASE_URL=postgresql://opspilot:opspilot@localhost:5432/opspilot \
        python scripts/load_synthetic.py --dir datasets/synthetic/procurement
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.analytics.db import get_engine
from modules.analytics.loader import load_directory


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="datasets/synthetic/procurement")
    ap.add_argument("--no-validate", action="store_true")
    ap.add_argument("--append", action="store_true", help="do not truncate existing rows first")
    a = ap.parse_args()
    counts = load_directory(get_engine(), a.dir, validate=not a.no_validate, replace=not a.append)
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
