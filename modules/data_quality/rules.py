"""Data-quality rules: is the *data* internally consistent? (Not: is the *business* behaving?)

Data-quality failures mean a record is wrong or inconsistent (a total that does not equal its lines,
a bank account that belongs to another supplier). Business exceptions (price variance, duplicates,
missing receipts) live in ``ops.v_invoice_exceptions``; an invoice can be a perfectly consistent
record and still be a bad invoice. Every rule is one SQL query returning violating rows as
(entity_id, detail).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class DQRule:
    rule_id: str
    severity: str          # error | warning
    description: str
    sql: str


RULES: list[DQRule] = [
    DQRule("invoice_subtotal_equals_lines", "error", "Invoice subtotal equals the sum of its line amounts (+-0.01)",
           """SELECT i.invoice_id AS entity_id, format('subtotal %s vs lines %s', i.subtotal, l.s) AS detail
              FROM ops.invoice i JOIN (SELECT invoice_id, sum(amount) s FROM ops.invoice_line GROUP BY 1) l USING (invoice_id)
              WHERE abs(i.subtotal - l.s) > 0.01"""),
    DQRule("invoice_total_equals_subtotal_plus_tax", "error", "Invoice total equals subtotal + tax (+-0.01)",
           """SELECT invoice_id, format('total %s vs %s', total, subtotal + tax) FROM ops.invoice
              WHERE abs(total - (subtotal + tax)) > 0.01"""),
    DQRule("invoice_line_amount_equals_qty_x_price", "error", "Line amount equals qty x unit price (+-0.01)",
           """SELECT invoice_id || '#' || line_no, format('amount %s vs %s', amount, round(qty * unit_price, 2))
              FROM ops.invoice_line WHERE abs(amount - round(qty * unit_price, 2)) > 0.01"""),
    DQRule("invoice_has_lines", "error", "Every invoice has at least one line",
           """SELECT i.invoice_id, 'no lines' FROM ops.invoice i
              WHERE NOT EXISTS (SELECT 1 FROM ops.invoice_line l WHERE l.invoice_id = i.invoice_id)"""),
    DQRule("po_has_lines", "error", "Every purchase order has at least one line",
           """SELECT p.po_number, 'no lines' FROM ops.purchase_order p
              WHERE NOT EXISTS (SELECT 1 FROM ops.po_line l WHERE l.po_number = p.po_number)"""),
    DQRule("receipt_line_po_matches_receipt", "error", "Receipt line references the same PO as its goods receipt",
           """SELECT rl.receipt_id || '#' || rl.line_no, format('line PO %s vs receipt PO %s', rl.po_number, gr.po_number)
              FROM ops.receipt_line rl JOIN ops.goods_receipt gr USING (receipt_id) WHERE rl.po_number <> gr.po_number"""),
    DQRule("invoice_line_po_matches_header", "error", "Invoice line PO equals the invoice header PO",
           """SELECT il.invoice_id || '#' || il.line_no, format('line PO %s vs header PO %s', il.po_number, coalesce(i.po_number, 'NULL'))
              FROM ops.invoice_line il JOIN ops.invoice i USING (invoice_id)
              WHERE il.po_number IS NOT NULL AND il.po_number IS DISTINCT FROM i.po_number"""),
    DQRule("invoice_supplier_matches_po_supplier", "error", "Invoice supplier equals the PO supplier",
           """SELECT i.invoice_id, format('invoice supplier %s vs PO supplier %s', i.supplier_id, po.supplier_id)
              FROM ops.invoice i JOIN ops.purchase_order po USING (po_number) WHERE i.supplier_id <> po.supplier_id"""),
    DQRule("invoice_currency_matches_po", "warning", "Invoice currency equals the PO currency",
           """SELECT i.invoice_id, format('%s vs %s', i.currency, po.currency)
              FROM ops.invoice i JOIN ops.purchase_order po USING (po_number) WHERE i.currency <> po.currency"""),
    DQRule("due_date_not_before_invoice_date", "error", "Due date is not before the invoice date",
           "SELECT invoice_id, format('due %s < invoice %s', due_date, invoice_date) FROM ops.invoice WHERE due_date < invoice_date"),
    DQRule("payment_not_before_approval", "error", "No payment is dated before the invoice was approved",
           """SELECT p.payment_id, format('paid %s, approved %s', p.paid_date, i.approved_at)
              FROM ops.payment p JOIN ops.invoice i USING (invoice_id)
              WHERE i.approved_at IS NOT NULL AND p.paid_date < (i.approved_at AT TIME ZONE 'America/Edmonton')::date"""),
    DQRule("payments_do_not_exceed_invoice_total", "error", "Payments on an invoice do not exceed its total",
           """SELECT i.invoice_id, format('paid %s vs total %s', s.paid, i.total)
              FROM ops.invoice i JOIN (SELECT invoice_id, sum(amount) paid FROM ops.payment GROUP BY 1) s USING (invoice_id)
              WHERE s.paid > i.total + 0.01"""),
    DQRule("bank_account_belongs_to_supplier", "error", "Invoice bank account belongs to the invoice supplier",
           """SELECT i.invoice_id, format('account %s belongs to %s', b.bank_account_id, b.supplier_id)
              FROM ops.invoice i JOIN ops.supplier_bank_account b USING (bank_account_id) WHERE b.supplier_id <> i.supplier_id"""),
    DQRule("bank_account_valid_on_invoice_date", "warning", "Bank account was valid (inclusive) on the invoice date",
           """SELECT i.invoice_id, format('account %s valid %s..%s, invoice %s', b.bank_account_id, b.valid_from, b.valid_to, i.invoice_date)
              FROM ops.invoice i JOIN ops.supplier_bank_account b USING (bank_account_id)
              WHERE i.invoice_date < b.valid_from OR (b.valid_to IS NOT NULL AND i.invoice_date > b.valid_to)"""),
    DQRule("goods_receipt_not_before_po", "warning", "Goods are not received before the PO date",
           """SELECT gr.receipt_id, format('received %s, PO %s', gr.received_date, po.order_date)
              FROM ops.goods_receipt gr JOIN ops.purchase_order po USING (po_number) WHERE gr.received_date < po.order_date"""),
    DQRule("employee_chain_terminates", "error", "Every employee's management chain ends at a top-level manager (no cycles)",
           """WITH RECURSIVE c AS (
                  SELECT employee_id, manager_id, 1 AS depth FROM ops.employee
                  UNION ALL SELECT c.employee_id, e.manager_id, c.depth + 1
                  FROM c JOIN ops.employee e ON e.employee_id = c.manager_id WHERE c.depth < 12)
              SELECT DISTINCT employee_id, 'management chain does not terminate' FROM c WHERE depth >= 12"""),
]


@dataclass
class DQResult:
    rule_id: str
    severity: str
    description: str
    violations: int
    sample: list[dict[str, Any]]

    @property
    def passed(self) -> bool:
        return self.violations == 0


@dataclass
class DQReport:
    run_at: str
    results: list[DQResult] = field(default_factory=list)

    @property
    def errors(self) -> list[DQResult]:
        return [r for r in self.results if r.severity == "error" and not r.passed]

    @property
    def warnings(self) -> list[DQResult]:
        return [r for r in self.results if r.severity == "warning" and not r.passed]

    def to_dict(self) -> dict[str, Any]:
        return {"run_at": self.run_at, "rules": len(self.results), "failed_errors": len(self.errors),
                "failed_warnings": len(self.warnings),
                "results": [{"rule_id": r.rule_id, "severity": r.severity, "description": r.description,
                             "violations": r.violations, "passed": r.passed, "sample": r.sample} for r in self.results]}


def run_rules(engine: Engine, rules: list[DQRule] | None = None, sample_size: int = 5) -> DQReport:
    report = DQReport(run_at=datetime.now(UTC).isoformat(timespec="seconds"))
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET TRANSACTION READ ONLY"))
            for rule in rules or RULES:
                n = conn.execute(text(f"SELECT count(*) FROM ({rule.sql}) AS v")).scalar_one()
                sample: list[dict[str, Any]] = []
                if n:
                    rows = conn.execute(text(f"SELECT * FROM ({rule.sql}) AS v LIMIT {int(sample_size)}")).fetchall()
                    sample = [{"entity_id": r[0], "detail": r[1]} for r in rows]
                report.results.append(DQResult(rule.rule_id, rule.severity, rule.description, int(n), sample))
    return report


def check_freshness(engine: Engine, max_age_days: int = 3, now: datetime | None = None) -> DQResult:
    """Pipeline freshness: has an invoice been received recently? (A stale feed is a data-quality problem.)"""
    now = now or datetime.now(UTC)
    with engine.connect() as conn:
        latest = conn.execute(text("SELECT max(received_at) FROM ops.invoice")).scalar_one()
    age = None if latest is None else (now - latest).days
    bad = latest is None or age > max_age_days
    detail = "no invoices loaded" if latest is None else f"latest invoice received {latest.isoformat()} ({age} days old)"
    return DQResult("data_freshness", "warning", f"Latest invoice received within {max_age_days} days",
                    int(bad), [{"entity_id": "ops.invoice", "detail": detail}] if bad else [])
