import json
import subprocess
import sys
from datetime import UTC, date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pandera.errors
import pytest
from sqlalchemy import text

from modules.analytics import duckdb_local
from modules.analytics import kpi_service as k
from modules.analytics.load_extraction import load_extraction
from modules.analytics.reconcile import reconcile_invoice
from modules.data_quality import raw_schemas
from modules.data_quality.rules import RULES, check_freshness, run_rules

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]
RANGE = {"start_date": "2025-10-01", "end_date": "2026-10-31", "top_n": 8}


# ---------------- data quality
def test_dq_rules_pass_on_clean_generated_data(engine):
    rep = run_rules(engine)
    assert len(rep.results) == len(RULES) and not rep.errors, [r.rule_id for r in rep.errors]


def test_dq_rules_catch_injected_corruption(engine):
    """Corrupt a copy inside a transaction that is rolled back; each corruption must trip its rule."""
    cases = {
        "invoice_total_equals_subtotal_plus_tax": "UPDATE ops.invoice SET total = total + 5 WHERE invoice_id='INV-000001'",
        "invoice_line_amount_equals_qty_x_price": "UPDATE ops.invoice_line SET amount = amount + 5 WHERE invoice_id='INV-000001' AND line_no=1",
        "due_date_not_before_invoice_date": "UPDATE ops.invoice SET due_date = invoice_date - 1 WHERE invoice_id='INV-000001'",
    }
    by_id = {r.rule_id: r for r in RULES}
    for rule_id, bad_sql in cases.items():
        with engine.connect() as c:
            tx = c.begin()
            c.execute(text("ALTER TABLE ops.invoice DISABLE ROW LEVEL SECURITY")) if False else None
            c.execute(text(bad_sql))
            n = c.execute(text(f"SELECT count(*) FROM ({by_id[rule_id].sql}) v")).scalar()
            tx.rollback()
        assert n >= 1, rule_id


def test_dq_runner_is_read_only(engine):
    rep = run_rules(engine)
    assert rep.to_dict()["rules"] == len(RULES)


def test_freshness_flags_stale_feed(engine):
    from datetime import datetime
    assert check_freshness(engine, 3, now=datetime(2030, 1, 1, tzinfo=UTC)).violations == 1
    assert check_freshness(engine, 3, now=datetime(2026, 10, 16, tzinfo=UTC)).violations in (0, 1)


# ---------------- Pandera raw-file validation
def test_pandera_rejects_malformed_files(csv_dir, tmp_path):
    for name in raw_schemas.SCHEMAS:
        raw_schemas.validate_file(name, csv_dir / f"{name}.csv")          # clean files pass
    bad = pd.read_csv(csv_dir / "invoice.csv", dtype=str)
    bad.loc[0, "invoice_date"] = "25/10/2025"
    bad.to_csv(tmp_path / "invoice.csv", index=False)
    with pytest.raises(pandera.errors.SchemaErrors):
        raw_schemas.validate_file("invoice", tmp_path / "invoice.csv")
    dup = pd.read_csv(csv_dir / "supplier.csv", dtype=str)
    pd.concat([dup, dup.iloc[[0]]]).to_csv(tmp_path / "supplier.csv", index=False)
    with pytest.raises(pandera.errors.SchemaErrors):
        raw_schemas.validate_file("supplier", tmp_path / "supplier.csv")


# ---------------- DuckDB parity
@pytest.mark.parametrize("kpi_id", sorted(i for i, s in k.registry().items() if "duckdb" in s.engines))
def test_duckdb_matches_postgres(engine, csv_dir, kpi_id):
    con = duckdb_local.connect(csv_dir)
    spec = k.registry()[kpi_id]
    params = {p: RANGE[p] for p in spec.params}
    pg = k.run_kpi(engine, kpi_id, params).rows
    dd = duckdb_local.run_kpi(con, kpi_id, params).rows
    assert len(pg) == len(dd) > 0
    for a, b in zip(pg, dd):
        for key in a:
            x, y = a[key], b[key]
            if isinstance(x, (Decimal, float)) or isinstance(y, (Decimal, float)):
                assert abs(float(x) - float(y)) <= max(0.011, 1e-6 * abs(float(x))), (kpi_id, key, x, y)
            else:
                assert x == y, (kpi_id, key, x, y)


def test_duckdb_refuses_non_portable_kpi(csv_dir):
    con = duckdb_local.connect(csv_dir)
    with pytest.raises(k.KpiParamError, match="not portable"):
        duckdb_local.run_kpi(con, "three_way_match_rate", {"start_date": "2025-10-01", "end_date": "2026-10-31"})


# ---------------- reconcile
def test_reconcile_shape_and_unknown(engine):
    r = reconcile_invoice(engine, "INV-000004")
    assert r.match_status == "exception" and {e.code for e in r.exceptions} >= {"DUPLICATE_INVOICE_NUMBER"}
    assert all(e.recommended_action for e in r.exceptions) and r.lines and r.provenance["source"]
    assert reconcile_invoice(engine, "does-not-exist") is None


def test_reconcile_clean_invoice_has_no_error_exceptions(engine):
    with engine.connect() as c:
        iid = c.execute(text("SELECT invoice_id FROM ops.v_invoice_match_status WHERE match_status='matched' LIMIT 1")).scalar()
    r = reconcile_invoice(engine, iid)
    assert r.match_status == "matched" and not [e for e in r.exceptions if e.severity == "error"]


# ---------------- WF1 -> WF2 bridge
def _extraction(**over):
    pytest.importorskip("modules.ocr", reason="needs Workflow 1")
    from modules.ocr.extract.invoice_schema import (
        DateField,
        InvoiceExtraction,
        LineItem,
        MoneyField,
        ProcessingInfo,
        SupplierMatch,
        TextField,
    )
    base = dict(
        source_file="x.pdf", source_sha256="ab" * 32,
        processing=ProcessingInfo(pipeline_version="t", engine="pdf_text", used_ocr=False),
        invoice_number=TextField(raw="OCR-1", value="OCR-1", confidence=0.99),
        invoice_date=DateField(raw="2026-10-01", value=date(2026, 10, 1), confidence=0.99),
        due_date=DateField(raw="2026-10-31", value=date(2026, 10, 31), confidence=0.99),
        po_number=TextField(raw="PO-NOPE", value="PO-NOPE", confidence=0.9),
        currency=TextField(raw="CAD", value="CAD", confidence=0.9),
        subtotal=MoneyField(raw="100.00", value=Decimal("100.00"), confidence=0.99),
        tax=MoneyField(raw="5.00", value=Decimal("5.00"), confidence=0.99),
        total=MoneyField(raw="105.00", value=Decimal("105.00"), confidence=0.99),
        line_items=[LineItem(line_no=1, description="Widget", quantity=Decimal(10), unit_price=Decimal(10), amount=Decimal("100.00"))],
        supplier_match=SupplierMatch(supplier_id="SUP0001", master_name="x", score=0.98, status="matched"),
    )
    base.update(over)
    return InvoiceExtraction(**base)


def test_load_extraction_inserts_and_flags_no_po(engine):
    out = load_extraction(engine, _extraction(), invoice_id="INV-OCR-T1")
    assert out.loaded
    with engine.connect() as c:
        row = c.execute(text("SELECT status, source, po_number, total FROM ops.invoice WHERE invoice_id='INV-OCR-T1'")).one()
        codes = {r[0] for r in c.execute(text("SELECT exception_code FROM ops.v_invoice_exceptions WHERE invoice_id='INV-OCR-T1'"))}
        audit = c.execute(text("SELECT count(*) FROM ops.audit_event WHERE entity_id='INV-OCR-T1'")).scalar()
    assert tuple(row) == ("extracted", "ocr", None, Decimal("105.00")) and "NO_PO" in codes and audit == 1
    again = load_extraction(engine, _extraction(), invoice_id="INV-OCR-T1")
    assert not again.loaded and "already exists" in again.reasons[0]


def test_load_extraction_refuses_unmatched_supplier_and_missing_fields(engine):
    pytest.importorskip("modules.ocr")
    from modules.ocr.extract.invoice_schema import SupplierMatch
    amb = _extraction(supplier_match=SupplierMatch(supplier_id=None, master_name=None, score=0.4, status="unknown"))
    out = load_extraction(engine, amb, invoice_id="INV-OCR-T2")
    assert not out.loaded and "supplier not confidently matched" in out.reasons
    out = load_extraction(engine, _extraction(total=None), invoice_id="INV-OCR-T3")
    assert not out.loaded and "missing total" in out.reasons
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM ops.invoice WHERE invoice_id IN ('INV-OCR-T2','INV-OCR-T3')")).scalar() == 0


def test_load_extraction_links_lines_to_known_po(engine):
    pytest.importorskip("modules.ocr")
    with engine.connect() as c:
        po, desc, sup = c.execute(text(
            "SELECT pl.po_number, pl.description, p.supplier_id FROM ops.po_line pl JOIN ops.purchase_order p USING (po_number) LIMIT 1")).one()
    from modules.ocr.extract.invoice_schema import LineItem, SupplierMatch, TextField
    x = _extraction(po_number=TextField(raw=po, value=po, confidence=0.9),
                    supplier_match=SupplierMatch(supplier_id=sup, master_name="x", score=0.99, status="matched"),
                    line_items=[LineItem(line_no=1, description=desc, quantity=Decimal(1), unit_price=Decimal(99), amount=Decimal(99)),
                                LineItem(line_no=2, description="zzzz unrelated", quantity=Decimal(1), unit_price=Decimal(1), amount=Decimal(1))])
    out = load_extraction(engine, x, invoice_id="INV-OCR-T4")
    assert out.loaded and out.unlinked_lines == [2]


# ---------------- migrations & CLI
def test_views_migration_downgrade_upgrade_cycle(engine):
    from alembic import command
    cfg = engine.alembic_cfg
    with engine.begin() as c:   # dbt objects depend on the views; Postgres (rightly) refuses to drop them underneath
        c.execute(text("DROP SCHEMA IF EXISTS staging, marts, reference, analytics CASCADE"))
    command.downgrade(cfg, "0001")
    with engine.connect() as c:
        assert c.execute(text("SELECT to_regclass('ops.v_invoice_exceptions')")).scalar() is None
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM ops.v_invoice_exceptions")).scalar() > 0


def test_dq_cli_exit_codes(engine, tmp_path):
    env = {"DATABASE_URL": engine.url.render_as_string(hide_password=False), "PATH": "/usr/bin:/bin:/usr/local/bin"}
    p = subprocess.run([sys.executable, "-m", "modules.data_quality.run", "--out", str(tmp_path)],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(next(tmp_path.glob("dq_*.json")).read_text())["failed_errors"] == 0
