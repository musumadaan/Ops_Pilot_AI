import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.db


def _as(engine, role, user, sql):
    with engine.connect() as c, c.begin():
        c.execute(text(f"SET LOCAL ROLE {role}"))
        c.execute(text("SELECT set_config('app.user', :u, true)"), {"u": user})
        return c.execute(text(sql)).fetchall()


def test_analyst_sees_only_assigned_suppliers(engine):
    rows = _as(engine, "ops_analyst", "analyst_a", "SELECT DISTINCT supplier_id FROM ops.invoice")
    with engine.connect() as c:
        allowed = {r[0] for r in c.execute(text("SELECT supplier_id FROM ops.user_supplier_access WHERE user_name='analyst_a'"))}
        total = c.execute(text("SELECT count(DISTINCT supplier_id) FROM ops.invoice")).scalar()
    seen = {r[0] for r in rows}
    assert seen and seen <= allowed and len(seen) < total


def test_unknown_user_sees_nothing(engine):
    assert _as(engine, "ops_analyst", "nobody", "SELECT * FROM ops.invoice") == []
    assert _as(engine, "ops_analyst", "", "SELECT * FROM ops.invoice") == []


def test_views_respect_rls(engine):
    rows = _as(engine, "ops_analyst", "analyst_a", "SELECT DISTINCT supplier_id FROM ops.v_invoice_match_status")
    with engine.connect() as c:
        allowed = {r[0] for r in c.execute(text("SELECT supplier_id FROM ops.user_supplier_access WHERE user_name='analyst_a'"))}
    assert {r[0] for r in rows} <= allowed


def test_finance_admin_sees_all(engine):
    n = _as(engine, "ops_finance_admin", "x", "SELECT count(*) FROM ops.invoice")[0][0]
    with engine.connect() as c:
        assert n == c.execute(text("SELECT count(*) FROM ops.invoice")).scalar()


def test_analyst_cannot_write(engine):
    with pytest.raises(DBAPIError):
        _as(engine, "ops_analyst", "analyst_a", "UPDATE ops.invoice SET status='paid'")


def test_audit_event_is_append_only(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO ops.audit_event (actor, action, entity) VALUES ('t','test','x')"))
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as c:
        c.execute(text("UPDATE ops.audit_event SET actor='evil'"))
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as c:
        c.execute(text("DELETE FROM ops.audit_event"))


def test_ensure_audit_partition_is_idempotent(engine):
    with engine.begin() as c:
        a = c.execute(text("SELECT ops.ensure_audit_partition(DATE '2031-01-01')")).scalar()
        b = c.execute(text("SELECT ops.ensure_audit_partition(DATE '2031-01-01')")).scalar()
        c.execute(text("INSERT INTO ops.audit_event (event_time, actor, action, entity) VALUES ('2031-01-15','t','p','x')"))
        tbl = c.execute(text("SELECT tableoid::regclass::text FROM ops.audit_event WHERE event_time='2031-01-15'")).scalar()
    assert a == b == "audit_event_2031_01" and tbl.endswith("audit_event_2031_01")


def test_constraints_reject_bad_rows(engine):
    with pytest.raises(DBAPIError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO ops.po_line (po_number, line_no, description, qty_ordered, unit_price) "
                           "SELECT po_number, 99, 'x', -1, 1 FROM ops.purchase_order LIMIT 1"))
    with pytest.raises(DBAPIError):
        with engine.begin() as c:
            c.execute(text("UPDATE ops.invoice SET status='bogus' WHERE invoice_id=(SELECT min(invoice_id) FROM ops.invoice)"))


def test_approval_chain_and_required_approver(engine):
    with engine.connect() as c:
        chain = c.execute(text("SELECT level, employee_id, approval_limit FROM ops.approval_chain('E017')")).fetchall()
        assert [r[0] for r in chain] == list(range(len(chain))) and len(chain) >= 2
        limits = [r[2] for r in chain]
        assert limits == sorted(limits)                       # authority never decreases going up
        assert c.execute(text("SELECT ops.required_approver('E017', 1)")).scalar() == "E017"
        assert c.execute(text("SELECT ops.required_approver('E017', 1e12)")).scalar() is None
