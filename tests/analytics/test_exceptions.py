import pandas as pd
import pytest

from .oracle import expected_exceptions

pytestmark = pytest.mark.db


def test_sql_matches_pandas_oracle(engine, csv_dir):
    sql = pd.read_sql("SELECT invoice_id, exception_code FROM ops.v_invoice_exceptions", engine)
    got = set(map(tuple, sql.to_numpy()))
    want = expected_exceptions(csv_dir)
    assert not (want - got), f"SQL missed: {sorted(want - got)[:10]}"
    assert not (got - want), f"SQL extra: {sorted(got - want)[:10]}"


def test_every_injected_defect_is_detected(engine, csv_dir):
    labels = pd.read_csv(csv_dir / "labels/anomaly_labels.csv").dropna(subset=["expected_code"])
    got = set(map(tuple, pd.read_sql("SELECT invoice_id, exception_code FROM ops.v_invoice_exceptions", engine).to_numpy()))
    missed = [(r.invoice_id, r.expected_code) for r in labels.itertuples() if (r.invoice_id, r.expected_code) not in got]
    assert not missed, missed


def test_clean_invoices_exist_and_status_is_consistent(engine):
    df = pd.read_sql("SELECT match_status, count(*) n FROM ops.v_invoice_match_status GROUP BY 1", engine).set_index("match_status")["n"]
    assert df.get("matched", 0) > df.get("exception", 0) > 0


def test_exception_codes_all_have_reference_rows(engine):
    n = pd.read_sql("SELECT count(*) n FROM ops.v_invoice_exceptions e LEFT JOIN ops.exception_code c ON c.code=e.exception_code WHERE c.code IS NULL", engine).n[0]
    assert n == 0


def test_control_parameter_change_changes_result(engine):
    base = pd.read_sql("SELECT count(*) n FROM ops.v_invoice_exceptions WHERE exception_code='ROUND_AMOUNT'", engine).n[0]
    with engine.begin() as c:
        c.exec_driver_sql("UPDATE ops.control_parameter SET value = 500 WHERE key='round_amount_step'")
    try:
        more = pd.read_sql("SELECT count(*) n FROM ops.v_invoice_exceptions WHERE exception_code='ROUND_AMOUNT'", engine).n[0]
    finally:
        with engine.begin() as c:
            c.exec_driver_sql("UPDATE ops.control_parameter SET value = 1000 WHERE key='round_amount_step'")
    assert more >= base
