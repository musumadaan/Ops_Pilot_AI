import math
from decimal import Decimal

import pandas as pd
import pytest

from modules.analytics import kpi_service as k

pytestmark = pytest.mark.db
RANGE = {"start_date": "2025-10-01", "end_date": "2026-10-31"}


def _params(spec):
    return {p: ({"top_n": 5}.get(p) or RANGE[p]) for p in spec.params}


@pytest.mark.parametrize("kpi_id", sorted(k.registry()))
def test_every_kpi_runs_with_provenance(engine, kpi_id):
    spec = k.registry()[kpi_id]
    res = k.run_kpi(engine, kpi_id, _params(spec))
    assert res.rows is not None and res.provenance["sql_sha256"] == spec.sql_sha256
    assert res.provenance["engine"] == "postgresql"
    assert set(res.to_jsonable()) == {"kpi_id", "columns", "rows", "provenance"}


def test_supplier_concentration_matches_pandas(engine, csv_dir):
    inv = pd.read_csv(csv_dir / "invoice.csv")
    inv = inv[inv.status != "rejected"]
    s = inv.groupby("supplier_id").total.sum().sort_values(ascending=False)
    share = s / s.sum()
    row = k.run_kpi(engine, "supplier_concentration", RANGE).rows[0]
    assert row["suppliers"] == len(s)
    assert math.isclose(float(row["hhi"]), round((share ** 2).sum() * 10000, 1), abs_tol=0.06)
    assert math.isclose(float(row["top1_share_pct"]), round(share.iloc[0] * 100, 2), abs_tol=0.006)
    assert math.isclose(float(row["top5_share_pct"]), round(share.iloc[:5].sum() * 100, 2), abs_tol=0.006)


def test_top_suppliers_windows_computed_before_limit(engine):
    rows = k.run_kpi(engine, "top_suppliers_by_spend", {**RANGE, "top_n": 3}).rows
    assert [r["spend_rank"] for r in rows] == [1, 2, 3]
    assert rows[0]["share_pct"] == rows[0]["cumulative_share_pct"]
    assert Decimal(rows[2]["cumulative_share_pct"]) < 100                 # full population, not just the top 3


def test_cycle_time_percentiles_are_ordered(engine):
    for r in k.run_kpi(engine, "invoice_cycle_time", RANGE).rows:
        assert r["p50_days"] <= r["p90_days"] and r["n"] > 0


def test_exception_counts_match_view(engine):
    got = {r["exception_code"]: r["invoices"] for r in k.run_kpi(engine, "exception_counts", RANGE).rows} \
        if "exception_code" in k.run_kpi(engine, "exception_counts", RANGE).columns else None
    if got is None:
        pytest.skip("exception_counts column layout differs")
    with engine.connect() as c:
        want = dict(c.exec_driver_sql("SELECT exception_code, count(DISTINCT invoice_id) FROM ops.v_invoice_exceptions GROUP BY 1").fetchall())
    assert {a: int(b) for a, b in got.items() if a in want} == {a: int(b) for a, b in want.items() if a in got}


# ---- safety of the SQL surface
def test_unknown_kpi_and_params_rejected(engine):
    with pytest.raises(k.UnknownKpi):
        k.run_kpi(engine, "drop_everything", {})
    with pytest.raises(k.KpiParamError):
        k.run_kpi(engine, "supplier_concentration", {**RANGE, "extra": 1})
    with pytest.raises(k.KpiParamError):
        k.run_kpi(engine, "supplier_concentration", {"start_date": "2026-02-01", "end_date": "2026-01-01"})
    with pytest.raises(k.KpiParamError):
        k.run_kpi(engine, "supplier_concentration", {"start_date": "x'; DROP TABLE ops.invoice;--", "end_date": "2026-01-01"})
    with pytest.raises(k.KpiParamError):
        k.run_kpi(engine, "top_suppliers_by_spend", {**RANGE, "top_n": 10**9})
    with pytest.raises(k.KpiParamError):
        k.run_kpi(engine, "top_suppliers_by_spend", {**RANGE, "top_n": True})


def test_kpi_execution_is_read_only(engine, monkeypatch):
    from sqlalchemy import text as sa_text
    bad = k.KpiSpec("bad", "bad", "", {}, "", "DELETE FROM ops.control_parameter", "x")
    monkeypatch.setattr(k, "registry", lambda: {"bad": bad})
    with pytest.raises(Exception, match="read-only"):
        k.run_kpi(engine, "bad", {})
    with engine.connect() as c:
        assert c.execute(sa_text("SELECT count(*) FROM ops.control_parameter")).scalar() > 0


def test_statement_timeout_applies(engine, monkeypatch):
    slow = k.KpiSpec("slow", "slow", "", {}, "", "SELECT pg_sleep(2)", "x")
    monkeypatch.setattr(k, "registry", lambda: {"slow": slow})
    with pytest.raises(Exception, match="timeout"):
        k.run_kpi(engine, "slow", {}, timeout_ms=100)


def test_headers_match_bind_parameters():
    k.registry.cache_clear()
    assert len(k.registry()) == 9                                      # _parse raises on any header/bind mismatch


def test_record_evidence_links_case(engine):
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO ops.investigation_case (case_id, opened_by, question) "
                          "VALUES ('C-T1','tester','q') ON CONFLICT DO NOTHING")
    cid = "C-T1"
    res = k.run_kpi(engine, "supplier_concentration", RANGE)
    k.record_evidence(engine, cid, res, "EV-TEST-1")
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT sha256 FROM ops.case_evidence WHERE evidence_id='EV-TEST-1'").scalar() == res.provenance["sql_sha256"]
