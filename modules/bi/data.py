"""Assemble the dashboard dataset from the KPI service (the same audited queries the agent uses)."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text

from modules.analytics.kpi_service import registry, run_kpi

from .telemetry import summarize

DRILL_LIMIT = 1000


def _num(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _rows(res) -> list[dict]:
    return [{k: _num(v) for k, v in r.items()} for r in res.rows]


def build_dataset(engine: Engine, start: str = "2025-01-01", end: str = "2026-12-31", cases=None, ml_report: dict | None = None) -> dict:
    rng = {"start_date": start, "end_date": end}
    def go(name, extra):
        wanted = registry()[name].params                     # each KPI declares which parameters it takes
        return run_kpi(engine, name, {p: v for p, v in {**rng, **extra}.items() if p in wanted})

    k = {name: go(name, extra) for name, extra in [
        ("spend_variance_pct", {}), ("invoice_cycle_time", {}), ("three_way_match_rate", {}), ("exception_counts", {}),
        ("top_suppliers_by_spend", {"top_n": 10}), ("supplier_concentration", {}), ("payment_timeliness", {}),
        ("open_commitments", {}), ("approval_load", {})]}
    kp = {n: _rows(r) for n, r in k.items()}

    spend = defaultdict(lambda: {"actual": 0.0, "budget": 0.0})
    for r in kp["spend_variance_pct"]:
        spend[r["month"]]["actual"] += r["actual"] or 0.0
        spend[r["month"]]["budget"] += r["budget"] or 0.0
    monthly_spend = [{"month": m, **v} for m, v in sorted(spend.items())]

    inv = sum(r["invoices"] for r in kp["three_way_match_rate"]) or 0
    matched = sum(r["matched"] for r in kp["three_way_match_rate"])
    cyc = kp["invoice_cycle_time"]
    paid = sum(r["paid_invoices"] for r in kp["payment_timeliness"])
    tiles = {
        "invoices": inv,
        "match_rate_pct": round(100 * matched / inv, 1) if inv else None,
        "cycle_p50_days": round(sum(r["p50_days"] for r in cyc) / len(cyc), 2) if cyc else None,
        "on_time_pct": round(sum(r["on_time_pct"] * r["paid_invoices"] for r in kp["payment_timeliness"]) / paid, 1) if paid else None,
        "hhi": kp["supplier_concentration"][0]["hhi"] if kp["supplier_concentration"] else None,
        "open_commitment": round(sum(r["open_commitment"] for r in kp["open_commitments"]), 2),
    }
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT e.invoice_id, i.supplier_id, s.name AS supplier, i.invoice_number, i.po_number, i.invoice_date, i.total, i.status,
                   e.exception_code, e.severity, e.detail, x.recommended_action
            FROM ops.v_invoice_exceptions e
            JOIN ops.invoice i USING (invoice_id) JOIN ops.supplier s ON s.supplier_id = i.supplier_id
            JOIN ops.exception_code x ON x.code = e.exception_code
            WHERE i.invoice_date BETWEEN :s AND :e
            ORDER BY CASE e.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, i.total DESC, e.invoice_id
        """), {"s": start, "e": end}).mappings().all()
    drill: dict[str, dict] = {}
    for r in rows:
        d = drill.setdefault(r["invoice_id"], {"invoice_id": r["invoice_id"], "supplier_id": r["supplier_id"], "supplier": r["supplier"],
                                               "invoice_number": r["invoice_number"], "po_number": r["po_number"], "date": _num(r["invoice_date"]),
                                               "total": _num(r["total"]), "status": r["status"], "worst": r["severity"], "exceptions": []})
        d["exceptions"].append({"code": r["exception_code"], "severity": r["severity"], "detail": r["detail"], "action": r["recommended_action"]})
    return {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "period": {"start": start, "end": end}, "tiles": tiles,
            "monthly_spend": monthly_spend, "kpis": kp, "drill": list(drill.values())[:DRILL_LIMIT], "drill_truncated": len(drill) > DRILL_LIMIT,
            "agent": summarize(cases or []), "ml": (ml_report or {}).get("metrics"), "provenance": {n: r.provenance for n, r in k.items()}}
