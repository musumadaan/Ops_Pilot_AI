"""Run all data-quality rules and write a JSON report.

    python -m modules.data_quality.run --out modules/data_quality/reports --fail-on error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from modules.analytics.db import get_engine

from .rules import run_rules


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="opspilot-dq")
    ap.add_argument("--out", default="modules/data_quality/reports")
    ap.add_argument("--fail-on", choices=["error", "warning", "never"], default="error")
    a = ap.parse_args(argv)
    report = run_rules(get_engine())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = report.run_at.replace(":", "").replace("-", "")
    (out / f"dq_{stamp}.json").write_text(json.dumps(report.to_dict(), indent=2))
    for r in report.results:
        mark = "ok  " if r.passed else ("FAIL" if r.severity == "error" else "warn")
        print(f"[{mark}] {r.rule_id:<45} {r.violations}")
    failed = bool(report.errors) or (a.fail_on == "warning" and bool(report.warnings))
    return 1 if failed and a.fail_on != "never" else 0


if __name__ == "__main__":
    sys.exit(main())
