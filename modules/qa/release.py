"""Release gate: collect metrics from every workflow, check them against gates.json, write a report, exit non-zero on a blocker.

    python -m modules.qa.release --out modules/qa/reports
    python -m modules.qa.release --update-baseline      # accept current metrics as the regression baseline

Two kinds of check: absolute thresholds (gates.json) and regression against the stored baseline (no higher-is-better
metric may fall by more than 0.03). Metrics that cannot be computed are reported as SKIPPED, never silently passed.
"""
from __future__ import annotations

import argparse
import json
import operator
import tempfile
from datetime import date
from pathlib import Path

from . import adversarial
from .risk_register import to_markdown, untested_controls
from .trace import Tracer

HERE = Path(__file__).parent
OPS = {">=": operator.ge, "<=": operator.le, "==": operator.eq, ">": operator.gt, "<": operator.lt}
REGRESSION_TOL = 0.03
HIGHER_IS_BETTER = ("rag.recall@5", "rag.mrr", "rag.citation_correct_rate", "ml.pr_auc_lift", "ml.recall_at_5pct")


def rag_metrics() -> dict[str, float]:
    from modules.rag.corpus import write_corpus
    from modules.rag.evaluate import run
    with tempfile.TemporaryDirectory() as t:
        write_corpus(Path(t))
        rep = run(t)
    h = rep["ablation"]["hybrid"]
    return {"rag.recall@5": h["recall@5"], "rag.mrr": h["mrr"], **{f"rag.{k}": v for k, v in rep["answers"].items()}}


def ml_metrics() -> dict[str, float]:
    from modules.analytics.synthetic import Generator
    from modules.ml.anomaly import time_split, train
    from modules.ml.drift import drift_report
    from modules.ml.features import FEATURES, build_features
    from modules.ml.forecast import backtest, monthly_spend
    with tempfile.TemporaryDirectory() as t:
        d = Path(t) / "data"
        Generator(Path("datasets/synthetic/suppliers.csv"), 7, n_po=3000, per_type=12, start=date(2023, 10, 1)).write(d)
        df = build_features(d)
        res = train(df, root=Path(t) / "reg")
        tr, te = time_split(df)
        major = int((drift_report(tr, te, FEATURES)["status"] == "major").sum())
        best = res.metrics[res.chosen]
        series = monthly_spend(d).set_index("month")["subtotal"].asfreq("MS", fill_value=0.0).iloc[:-1]
        bt, _ = backtest(series)
    return {"ml.pr_auc_lift": best["pr_auc"] / best["prevalence"], "ml.recall_at_5pct": best["recall@5%"], "ml.major_drift_features": float(major),
            "ml.forecast_smape": float(bt["smape"].min()),
            "ml.forecast_skill_vs_naive": float(1 - bt["smape"].min() / bt.loc["seasonal_naive", "smape"])}


def evaluate_gates(metrics: dict[str, float], gates: list[dict], baseline: dict[str, float] | None = None) -> list[dict]:
    results = []
    for g in gates:
        v = metrics.get(g["metric"])
        if v is None:
            results.append({**g, "actual": None, "status": "SKIPPED"})
            continue
        results.append({**g, "actual": v, "status": "PASS" if OPS[g["op"]](v, g["value"]) else "FAIL"})
    if baseline:
        for m in HIGHER_IS_BETTER:
            if m in metrics and m in baseline:
                ok = metrics[m] >= baseline[m] - REGRESSION_TOL
                results.append({"id": f"regression:{m}", "metric": m, "op": ">=", "value": round(baseline[m] - REGRESSION_TOL, 4), "severity": "blocker",
                                "why": "No regression against the accepted baseline", "actual": metrics[m], "status": "PASS" if ok else "FAIL"})
    return results


def verdict(results: list[dict]) -> str:
    if any(r["status"] == "FAIL" and r["severity"] == "blocker" for r in results):
        return "BLOCKED"
    if any(r["status"] == "FAIL" for r in results):
        return "PASS_WITH_WARNINGS"
    return "PASS"


def collect(skip: set[str] = frozenset()) -> tuple[dict[str, float], Tracer]:
    tracer = Tracer()
    metrics: dict[str, float] = {}
    for name, fn in (("rag", rag_metrics), ("ml", ml_metrics), ("adversarial", adversarial.run_all)):
        if name in skip:
            continue
        with tracer.span(f"collect.{name}"):
            metrics.update(fn())
    metrics["risk.untested_controls"] = float(len(untested_controls()))
    return metrics, tracer


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "reports"))
    ap.add_argument("--skip", nargs="*", default=[], choices=["rag", "ml", "adversarial"])
    ap.add_argument("--update-baseline", action="store_true")
    a = ap.parse_args(argv)
    metrics, tracer = collect(set(a.skip))
    gates = json.loads((HERE / "gates.json").read_text())
    bp = HERE / "baseline.json"
    baseline = json.loads(bp.read_text()) if bp.exists() else None
    results = evaluate_gates(metrics, gates, baseline)
    v = verdict(results)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "release_report.json").write_text(json.dumps({"verdict": v, "metrics": metrics, "gates": results,
                                                          "spans": [{k: s[k] for k in ("name", "duration_ms", "status")} for s in tracer.spans]}, indent=2))
    (out / "risk_register.md").write_text(to_markdown())
    for r in results:
        print(f"[{r['status']:<7}] {r['id']:<26} {r['metric']:<34} {r['actual'] if r['actual'] is None else round(r['actual'], 4)} {r['op']} {r['value']}")
    print(f"\nVERDICT: {v}")
    if a.update_baseline:
        bp.write_text(json.dumps({m: metrics[m] for m in HIGHER_IS_BETTER if m in metrics}, indent=2))
        print(f"baseline written to {bp}")
    return 1 if v == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
