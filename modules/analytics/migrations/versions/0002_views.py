"""three-way match views, exception rules, approval functions, monthly spend materialized view

Revision ID: 0002
Revises: 0001
"""
from alembic import op

from modules.analytics.db import read_sql, run_script, sql_files

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for f in sql_files("views"):          # numeric prefixes give the dependency order
        run_script(bind, read_sql(f))


def downgrade() -> None:
    for obj in (
        "MATERIALIZED VIEW IF EXISTS ops.mv_monthly_spend",
        "VIEW IF EXISTS ops.v_invoice_match_status",
        "VIEW IF EXISTS ops.v_invoice_exceptions",
        "VIEW IF EXISTS ops.v_invoice_line_match",
        "VIEW IF EXISTS ops.v_po_line_status",
    ):
        op.execute(f"DROP {obj}")
    op.execute("DROP FUNCTION IF EXISTS ops.required_approver(text, numeric)")
    op.execute("DROP FUNCTION IF EXISTS ops.approval_chain(text)")
