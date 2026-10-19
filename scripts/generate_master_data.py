"""Generate the synthetic procurement history (CSV files + labelled defects).

    python scripts/generate_master_data.py --out datasets/synthetic/procurement --seed 42 --n-po 400

Reuses datasets/synthetic/suppliers.csv (also used by Workflow 1) so both workflows share suppliers.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.analytics.synthetic import Generator


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suppliers", default="datasets/synthetic/suppliers.csv")
    ap.add_argument("--out", default="datasets/synthetic/procurement")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-po", type=int, default=400)
    ap.add_argument("--per-type", type=int, default=4, help="injected defects per anomaly type")
    a = ap.parse_args()
    report = Generator(Path(a.suppliers), a.seed, a.n_po, a.per_type).write(Path(a.out))
    print(json.dumps(report["rows"], indent=2))


if __name__ == "__main__":
    main()
