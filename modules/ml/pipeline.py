"""End-to-end ML run: generate data (or use a directory), train, evaluate, check drift, backtest forecasts.

    python -m modules.ml.pipeline --out modules/ml/reports
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

from . import registry
from .anomaly import time_split, train
from .drift import drift_report
from .features import FEATURES, build_features
from .forecast import backtest, forecast, monthly_spend


def make_training_data(out: Path, seed: int = 7) -> Path:
    from modules.analytics.synthetic import Generator
    Generator(Path("datasets/synthetic/suppliers.csv"), seed, n_po=3000, per_type=12, start=date(2023, 10, 1)).write(out)
    return out


def run(data_dir: Path, out: Path, registry_root: Path = registry.DEFAULT_ROOT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    df = build_features(data_dir)
    res = train(df, root=registry_root)
    tr, te = time_split(df)
    drift = drift_report(tr, te, FEATURES)
    series = monthly_spend(data_dir).set_index("month")["subtotal"].asfreq("MS", fill_value=0.0)
    series = series.iloc[:-1]                                            # drop the partial last month
    bt, _ = backtest(series)
    fc = forecast(series)
    report = {"model_version": res.version, "chosen": res.chosen, "metrics": res.metrics,
              "top_features": res.importances.head(8).to_dict("records"),
              "drift_train_vs_test": drift.to_dict("records"),
              "forecast_backtest": bt.reset_index().to_dict("records"),
              "forecast": fc.assign(month=fc["month"].dt.strftime("%Y-%m")).to_dict("records")}
    (out / "ml_report.json").write_text(json.dumps(report, indent=2, default=float))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="datasets/synthetic/procurement_ml")
    ap.add_argument("--out", default="modules/ml/reports")
    a = ap.parse_args()
    d = Path(a.data)
    if not (d / "invoice.csv").exists():
        make_training_data(d)
    rep = run(d, Path(a.out))
    print(json.dumps({k: rep[k] for k in ("model_version", "chosen", "metrics")}, indent=2, default=float))
    print(pd.DataFrame(rep["forecast_backtest"]).to_string(index=False))


if __name__ == "__main__":
    main()
