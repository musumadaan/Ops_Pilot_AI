"""Monthly spend forecasting with a rolling-origin backtest and honest baselines.

Three methods compete: seasonal-naive (same month last year), 3-month moving average, and a ridge regression on a
linear trend plus month-of-year dummies. A forecast is only worth shipping if it beats seasonal-naive in the
backtest; ``backtest`` reports that comparison and ``best_method`` picks by sMAPE.
Prediction intervals are empirical quantiles of backtest residuals (distribution-free, no normality assumption).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge


def monthly_spend(d: str | Path, by_category: bool = False) -> pd.DataFrame:
    d = Path(d)
    inv = pd.read_csv(d / "invoice.csv", dtype={"po_number": "string"})
    inv = inv[inv["status"] != "rejected"]
    inv["month"] = pd.to_datetime(inv["invoice_date"]).dt.to_period("M").dt.to_timestamp()
    if by_category:
        po = pd.read_csv(d / "purchase_order.csv")[["po_number", "category"]]
        inv = inv.merge(po, on="po_number", how="left").fillna({"category": "Uncategorized"})
        s = inv.groupby(["category", "month"])["subtotal"].sum().reset_index()
    else:
        s = inv.groupby("month")["subtotal"].sum().reset_index()
    return s


def _seasonal_naive(y: np.ndarray, h: int) -> np.ndarray:
    return np.array([y[-12 + (i % 12)] for i in range(h)]) if len(y) >= 12 else np.repeat(y[-1], h)


def _moving_avg(y: np.ndarray, h: int) -> np.ndarray:
    return np.repeat(y[-3:].mean(), h)


def _design(t: np.ndarray, months: np.ndarray) -> np.ndarray:
    return np.column_stack([t] + [(months == m).astype(float) for m in range(1, 13)])


def _ridge(y: np.ndarray, h: int, last_month: pd.Timestamp) -> np.ndarray:
    n = len(y)
    months = np.array([((last_month.month - n + i) % 12) + 1 for i in range(n + h)])
    X = _design(np.arange(n + h, dtype=float), months)
    scale = y.mean() or 1.0
    m = Ridge(alpha=1.0).fit(X[:n], y / scale)
    return np.maximum(m.predict(X[n:]) * scale, 0)


METHODS = {"seasonal_naive": lambda y, h, lm: _seasonal_naive(y, h),
           "moving_avg_3": lambda y, h, lm: _moving_avg(y, h),
           "ridge_trend_season": _ridge}


def smape(a: np.ndarray, f: np.ndarray) -> float:
    den = (np.abs(a) + np.abs(f)) / 2
    return float(np.mean(np.where(den == 0, 0, np.abs(a - f) / den)) * 100)


def backtest(series: pd.Series, horizon: int = 3, min_train: int = 18) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    y = series.to_numpy(float)
    idx = series.index
    rows, resid = [], {k: [] for k in METHODS}
    for origin in range(min_train, len(y) - horizon + 1):
        for name, fn in METHODS.items():
            f = fn(y[:origin], horizon, idx[origin - 1])
            a = y[origin:origin + horizon]
            rows.append({"method": name, "origin": idx[origin - 1], "smape": smape(a, f), "mae": float(np.mean(np.abs(a - f)))})
            resid[name].extend((a - f).tolist())
    df = pd.DataFrame(rows)
    return df.groupby("method")[["smape", "mae"]].mean().sort_values("smape"), {k: np.array(v) for k, v in resid.items()}


def forecast(series: pd.Series, horizon: int = 3, interval: float = 0.8) -> pd.DataFrame:
    summary, resid = backtest(series, horizon)
    best = summary.index[0]
    y = series.to_numpy(float)
    point = METHODS[best](y, horizon, series.index[-1])
    lo_q, hi_q = (1 - interval) / 2, 1 - (1 - interval) / 2
    r = resid[best]
    lo, hi = np.quantile(r, lo_q), np.quantile(r, hi_q)
    future = pd.date_range(series.index[-1] + pd.offsets.MonthBegin(1), periods=horizon, freq="MS")
    return pd.DataFrame({"month": future, "forecast": point, "lower": np.maximum(point + lo, 0), "upper": point + hi,
                         "method": best, "backtest_smape": summary.loc[best, "smape"]})
