"""Independent pandas implementation of the exception rules.

Reads the generated CSVs (not the database) and recomputes the (invoice_id, code) pairs that
``ops.v_invoice_exceptions`` should emit. If SQL and oracle disagree, one of them has a bug.
Money is compared in integer cents to avoid float noise.
"""
from __future__ import annotations
"""Test oracle = the shipped pandas rules (see modules/analytics/offline_rules.py)."""
from modules.analytics.offline_rules import expected_exceptions  # noqa: F401

import re
from pathlib import Path

import pandas as pd

TZ = "America/Edmonton"
P = dict(qty_tol=0, threshold=10000, band=0.8, split_days=7, dup_days=14, round_step=1000, default_tol=2.0)


def _cents(s: pd.Series) -> pd.Series:
    return (s.astype(float) * 100).round().astype("int64")


def expected_exceptions(d: Path) -> set[tuple[str, str]]:
    rd = lambda n, **k: pd.read_csv(d / f"{n}.csv", **k)
    inv = rd("invoice", dtype={"po_number": "string", "bank_account_id": "string"})
    il = rd("invoice_line")
    pol = rd("po_line")
    po = rd("purchase_order")
    rl = rd("receipt_line")
    ct = rd("contract")
    out: set[tuple[str, str]] = set()

    inv["received_at"] = pd.to_datetime(inv["received_at"], utc=True)
    inv["invoice_date"] = pd.to_datetime(inv["invoice_date"])
    inv["total_c"] = _cents(inv["total"])
    live = inv[inv["status"] != "rejected"].copy()
    live["norm"] = live["invoice_number"].map(lambda s: re.sub(r"[^A-Za-z0-9]", "", s).upper())

    # ---- line rules
    lines = il.merge(live[["invoice_id", "supplier_id", "invoice_date"]], on="invoice_id")
    lines = lines.sort_values(["invoice_date", "invoice_id", "line_no"])
    lines["cum"] = lines.groupby(["po_number", "po_line_no"], dropna=True)["qty"].cumsum()
    lines.loc[lines["po_line_no"].isna(), "cum"] = lines["qty"]  # unlinked: not used by rules
    recv = rl.groupby(["po_number", "po_line_no"], as_index=False)["qty_received"].sum()
    lines = lines.merge(pol.rename(columns={"unit_price": "po_price"}), on=["po_number", "line_no"], how="left",
                        suffixes=("", "_po")) if False else lines
    lines = lines.merge(
        pol[["po_number", "line_no", "qty_ordered", "unit_price"]].rename(
            columns={"line_no": "po_line_no", "unit_price": "po_price"}),
        on=["po_number", "po_line_no"], how="left")
    lines = lines.merge(recv, on=["po_number", "po_line_no"], how="left")
    lines["qty_received"] = lines["qty_received"].fillna(0)
    linked = lines["po_line_no"].notna()
    for r in lines[linked & (lines["qty_received"] == 0)].itertuples():
        out.add((r.invoice_id, "NO_RECEIPT"))
    for r in lines[(lines["qty_received"] > 0) & (lines["cum"] > lines["qty_received"] + P["qty_tol"])].itertuples():
        out.add((r.invoice_id, "QTY_OVER_RECEIPT"))
    for r in lines[lines["qty_ordered"].notna() & (lines["cum"] > lines["qty_ordered"])].itertuples():
        out.add((r.invoice_id, "OVER_ORDERED_QTY"))
    # price variance with contract tolerance (latest contract in force on the invoice date, by PO category)
    lines = lines.merge(po[["po_number", "category"]], on="po_number", how="left")
    ct = ct.assign(effective_from=pd.to_datetime(ct["effective_from"]), effective_to=pd.to_datetime(ct["effective_to"]))
    def tol(r):
        c = ct[(ct.supplier_id == r.supplier_id) & (ct.category == r.category) & (ct.effective_from <= r.invoice_date)
               & (ct.effective_to.isna() | (ct.effective_to >= r.invoice_date))].sort_values("effective_from")
        return float(c.iloc[-1].price_tolerance_pct) if len(c) else P["default_tol"]
    for r in lines[lines["po_price"].notna() & (lines["po_price"] > 0)].itertuples():
        var = round((float(r.unit_price) - float(r.po_price)) / float(r.po_price) * 100, 2)
        if abs(var) > tol(r):
            out.add((r.invoice_id, "PRICE_VARIANCE"))

    # ---- header rules
    for r in live[live["po_number"].isna()].itertuples():
        out.add((r.invoice_id, "NO_PO"))
    withpo = live[live["po_number"].notna()]
    unlinked = il.merge(withpo[["invoice_id"]], on="invoice_id")
    for r in unlinked[unlinked["po_line_no"].isna()].itertuples():
        out.add((r.invoice_id, "UNMATCHED_LINE"))
    m = withpo.merge(po[["po_number", "order_date"]], on="po_number")
    for r in m[m["invoice_date"] < pd.to_datetime(m["order_date"])].itertuples():
        out.add((r.invoice_id, "DATE_BEFORE_PO"))
    local = live["received_at"].dt.tz_convert(TZ)
    for iid in live.loc[local.dt.dayofweek >= 5, "invoice_id"]:
        out.add((iid, "WEEKEND_POSTING"))
    step = P["round_step"] * 100
    for r in live[(live["total_c"] >= step) & (live["total_c"] % step == 0)].itertuples():
        out.add((r.invoice_id, "ROUND_AMOUNT"))

    # ---- pattern rules
    s = live.sort_values(["received_at", "invoice_id"])
    s["rn"] = s.groupby(["supplier_id", "norm"]).cumcount()
    for iid in s.loc[s["rn"] > 0, "invoice_id"]:
        out.add((iid, "DUPLICATE_INVOICE_NUMBER"))
    s = live.sort_values(["invoice_date", "received_at", "invoice_id"])
    g = s.groupby(["supplier_id", "total_c"])
    s["gap"] = (s["invoice_date"] - g["invoice_date"].shift()).dt.days
    s["prev_norm"] = g["norm"].shift()
    for r in s[(s["gap"] <= P["dup_days"]) & (s["norm"] != s["prev_norm"])].itertuples():
        out.add((r.invoice_id, "POSSIBLE_DUPLICATE"))
    b = s[s["bank_account_id"].notna()].copy()
    b["prev"] = b.groupby("supplier_id")["bank_account_id"].shift()
    for r in b[b["prev"].notna() & (b["prev"] != b["bank_account_id"])].itertuples():
        out.add((r.invoice_id, "BANK_ACCOUNT_CHANGED"))
    lo, hi = P["band"] * P["threshold"] * 100, P["threshold"] * 100
    near = live[(live["total_c"] >= lo) & (live["total_c"] < hi)]
    for r in near.itertuples():
        n = ((near["supplier_id"] == r.supplier_id) & (near["invoice_date"] <= r.invoice_date)
             & (near["invoice_date"] >= r.invoice_date - pd.Timedelta(days=P["split_days"]))).sum()
        if n >= 2:
            out.add((r.invoice_id, "SPLIT_BELOW_THRESHOLD"))
    return out
