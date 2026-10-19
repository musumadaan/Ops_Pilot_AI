"""Invoice-level features, computed only from information available when the invoice arrives.

Everything that depends on history (supplier statistics, duplicate counts, previous bank account) is
computed *expanding* in time order and shifted, so a row never sees itself or the future. That is what
makes the time-split evaluation honest.

Honesty note: several features are close cousins of the SQL exception rules (price variance, quantity over
receipt). Labels in the synthetic data come from the same defect injector, so high scores here show the model
can *learn* the patterns, not that it will beat rules on real data. The point of the module is the pipeline:
leakage-safe features, time-split evaluation, calibrated review-queue ranking, drift checks, explanations.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TZ = "America/Edmonton"
FEATURES = [
    "log_total", "n_lines", "tax_ratio", "has_po", "max_abs_price_var_pct", "max_qty_over_receipt",
    "max_qty_over_ordered", "days_po_to_invoice", "invoice_before_po", "received_weekend", "received_hour",
    "is_round_1000", "supplier_total_z", "supplier_prior_n", "dup_total_14d", "bank_changed",
    "near_threshold_prior_7d",
]


def _read(d: Path, name: str, **kw) -> pd.DataFrame:
    return pd.read_csv(d / f"{name}.csv", **kw)


def load_labels(d: Path) -> pd.Series:
    p = d / "labels" / "anomaly_labels.csv"
    if not p.exists():
        return pd.Series(dtype=int)
    lab = pd.read_csv(p)
    return pd.Series(1, index=lab["invoice_id"].unique(), name="label")


def build_features(d: str | Path, threshold: float = 10_000.0) -> pd.DataFrame:
    d = Path(d)
    inv = _read(d, "invoice", dtype={"po_number": "string", "bank_account_id": "string"})
    il = _read(d, "invoice_line")
    pol = _read(d, "po_line")
    po = _read(d, "purchase_order")
    rl = _read(d, "receipt_line")

    inv = inv[inv["status"] != "rejected"].copy()
    inv["received_at"] = pd.to_datetime(inv["received_at"], utc=True)
    inv["invoice_date"] = pd.to_datetime(inv["invoice_date"])
    inv = inv.sort_values(["received_at", "invoice_id"]).reset_index(drop=True)

    # ---- line level: price variance, quantity vs receipt / order (cumulative per PO line, in time order)
    ln = il.merge(inv[["invoice_id", "received_at", "invoice_date"]], on="invoice_id")
    ln = ln.sort_values(["received_at", "invoice_id", "line_no"])
    key = ["po_number", "po_line_no"]
    ln["cum_qty"] = ln.groupby(key, dropna=True)["qty"].cumsum().where(ln["po_line_no"].notna(), ln["qty"])
    ln = ln.merge(pol.rename(columns={"line_no": "po_line_no", "unit_price": "po_price"})[key + ["qty_ordered", "po_price"]],
                  on=key, how="left")
    recv = rl.groupby(key, as_index=False)["qty_received"].sum()
    ln = ln.merge(recv, on=key, how="left")
    ln["qty_received"] = ln["qty_received"].fillna(0.0)
    ln["price_var"] = ((ln["unit_price"] - ln["po_price"]) / ln["po_price"] * 100).abs()
    ln["over_receipt"] = np.where(ln["po_line_no"].notna(), ln["cum_qty"] / ln["qty_received"].where(ln["qty_received"] > 0, 0.5), 0.0)
    ln["over_ordered"] = np.where(ln["qty_ordered"].notna(), ln["cum_qty"] / ln["qty_ordered"], 0.0)
    agg = ln.groupby("invoice_id").agg(
        n_lines=("line_no", "count"), max_abs_price_var_pct=("price_var", "max"),
        max_qty_over_receipt=("over_receipt", "max"), max_qty_over_ordered=("over_ordered", "max"))
    f = inv.merge(agg, on="invoice_id", how="left").merge(po[["po_number", "order_date"]], on="po_number", how="left")
    for c in ("max_abs_price_var_pct", "max_qty_over_receipt", "max_qty_over_ordered"):
        f[c] = f[c].fillna(0.0).clip(upper=10.0)
    f["n_lines"] = f["n_lines"].fillna(0)

    # ---- header level
    f["log_total"] = np.log1p(f["total"])
    f["tax_ratio"] = f["tax"] / f["subtotal"].replace(0, np.nan)
    f["tax_ratio"] = f["tax_ratio"].fillna(0.0)
    f["has_po"] = f["po_number"].notna().astype(int)
    f["days_po_to_invoice"] = (f["invoice_date"] - pd.to_datetime(f["order_date"])).dt.days.fillna(-1)
    f["invoice_before_po"] = (f["days_po_to_invoice"] < 0).astype(int) * f["has_po"]
    local = f["received_at"].dt.tz_convert(TZ)
    f["received_weekend"] = (local.dt.dayofweek >= 5).astype(int)
    f["received_hour"] = local.dt.hour
    cents = (f["total"] * 100).round().astype("int64")
    f["is_round_1000"] = ((cents % 100_000 == 0) & (cents >= 100_000)).astype(int)

    # ---- history-dependent (expanding, shifted: never includes the row itself)
    g = f.groupby("supplier_id")["log_total"]
    mean = g.transform(lambda s: s.shift().expanding().mean())
    std = g.transform(lambda s: s.shift().expanding().std())
    f["supplier_prior_n"] = f.groupby("supplier_id").cumcount()
    f["supplier_total_z"] = ((f["log_total"] - mean) / std.where(std > 0.05, 0.05)).fillna(0.0).clip(-6, 6)
    f.loc[f["supplier_prior_n"] < 5, "supplier_total_z"] = 0.0   # too little history to judge

    f["total_c"] = cents
    f["dup_total_14d"] = 0
    f["near_threshold_prior_7d"] = 0
    f["bank_changed"] = 0
    near = (f["total"] >= 0.8 * threshold) & (f["total"] < threshold)
    for _, idx in f.groupby("supplier_id").groups.items():
        sub = f.loc[idx]
        dates = sub["invoice_date"].to_numpy()
        tot = sub["total_c"].to_numpy()
        nr = near.loc[idx].to_numpy()
        dup = np.zeros(len(sub), dtype=int)
        nt = np.zeros(len(sub), dtype=int)
        for i in range(len(sub)):
            prior = slice(0, i)
            dd = (dates[i] - dates[prior]).astype("timedelta64[D]").astype(int)
            dup[i] = int(((tot[prior] == tot[i]) & (dd >= 0) & (dd <= 14)).sum())
            if nr[i]:
                nt[i] = int((nr[prior] & (dd >= 0) & (dd <= 7)).sum())
        f.loc[idx, "dup_total_14d"] = dup
        f.loc[idx, "near_threshold_prior_7d"] = nt
        acc = sub["bank_account_id"]
        prev = acc.where(acc.notna()).ffill().shift()
        f.loc[idx, "bank_changed"] = (prev.notna() & acc.notna() & (prev != acc)).astype(int).to_numpy()

    out = f.set_index("invoice_id")[["received_at", "supplier_id"] + FEATURES].copy()
    labels = load_labels(d)
    out["label"] = out.index.map(lambda i: int(i in labels.index))
    return out
