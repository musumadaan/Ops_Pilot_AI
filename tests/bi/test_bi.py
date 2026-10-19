import json
import re

import pytest

from modules.agent.models import Case, State
from modules.bi.data import build_dataset
from modules.bi.render import render, safe_json
from modules.bi.telemetry import summarize


def _case(state, rec="hold", tokens=0, gaps=0):
    c = Case("C", "INV-1", "a", "analyst", "q")
    c.state, c.recommendation = state, rec
    c.usage.update(tool_calls=5, llm_tokens=tokens)
    c.gaps = ["g"] * gaps
    return c


def test_telemetry_rates():
    cs = [_case(State.CLOSED_NO_ACTION, "proceed"), _case(State.EXECUTED), _case(State.REJECTED), _case(State.FAILED, "", gaps=1), _case(State.AWAITING_APPROVAL, tokens=2000)]
    s = summarize(cs, price_per_1k_tokens=0.5)
    assert s["cases"] == 5 and s["auto_cleared_rate"] == pytest.approx(0.2) and s["override_rate"] == pytest.approx(0.5)
    assert s["failure_rate"] == pytest.approx(0.2) and s["cost_per_case"] == pytest.approx(1.0 / 5) and s["avg_tool_calls"] == 5
    assert summarize([]) == {"cases": 0}


def test_safe_json_cannot_break_out_of_script_tag():
    out = safe_json({"x": "</script><img src=x onerror=alert(1)><!--", "y": "a&b"})
    assert "</script" not in out and "<img" not in out and "<!--" not in out
    assert json.loads(out)["x"].startswith("</script>")                       # still round-trips exactly


@pytest.mark.db
def test_dataset_matches_sources_and_renders(engine):
    ds = build_dataset(engine, "2025-01-01", "2026-12-31")
    assert set(ds["kpis"]) >= {"spend_variance_pct", "invoice_cycle_time", "exception_counts", "top_suppliers_by_spend"}
    with engine.connect() as c:
        n_inv = c.exec_driver_sql("SELECT count(*) FROM ops.invoice WHERE status <> 'rejected'").scalar()
        flagged = c.exec_driver_sql("SELECT count(DISTINCT invoice_id) FROM ops.v_invoice_exceptions").scalar()
    assert ds["tiles"]["invoices"] == n_inv and len(ds["drill"]) == flagged
    assert 0 < ds["tiles"]["match_rate_pct"] <= 100 and all(d["exceptions"] and d["worst"] for d in ds["drill"])
    assert all(v["sql_sha256"] for v in ds["provenance"].values())          # every number traceable to a query version
    html = render(ds)
    assert html.startswith("<!doctype html>") and "__DATA__" not in html and "http://" not in html.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in html                                           # self-contained: no CDN, no network
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, flags=re.DOTALL)
    assert json.loads(m.group(1))["period"]["start"] == "2025-01-01"


@pytest.mark.db
def test_hostile_supplier_name_is_inert(engine):
    with engine.begin() as c:
        c.exec_driver_sql("UPDATE ops.supplier SET name = '</script><img src=x onerror=alert(1)>' WHERE supplier_id = 'SUP0001'")
    try:
        html = render(build_dataset(engine))
    finally:
        with engine.begin() as c:
            c.exec_driver_sql("UPDATE ops.supplier SET name = 'Prairie Steel & Fabrication Ltd.' WHERE supplier_id = 'SUP0001'")
    assert "<img src=x" not in html and html.count("</script>") == html.count("<script")


@pytest.mark.db
def test_dashboard_runs_in_a_browser_without_errors(engine, tmp_path):
    sync = pytest.importorskip("playwright.sync_api")
    f = tmp_path / "d.html"
    f.write_text(render(build_dataset(engine, cases=[_case(State.EXECUTED), _case(State.CLOSED_NO_ACTION, "proceed")])), encoding="utf-8")
    errs = []
    with sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no browser: {exc}")
        pg = b.new_page(viewport={"width": 390, "height": 800})
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(f"file://{f}")
        rows = pg.locator("#rows tr").count()
        pg.click("#rows tr:first-child")
        detail = pg.inner_text("#d")
        pg.fill("#q", "SUP0001")
        filtered = pg.locator("#rows tr").count()
        overflow = pg.evaluate("document.documentElement.scrollWidth > innerWidth")
        agent = pg.inner_text("#agent")
        b.close()
    assert not errs and rows > 0 and "Next step:" in detail and 0 < filtered < rows and not overflow and "Cases" in agent
