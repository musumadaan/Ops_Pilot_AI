"""core, governance, reference tables and indexes

Revision ID: 0001
Revises:
"""
from alembic import op

from modules.analytics.db import read_sql, run_script

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

FILES = [
    "schema/001_core.sql",
    "schema/002_governance.sql",
    "schema/004_reference.sql",   # before indexes only for readability; no dependency
    "schema/003_indexes.sql",
]


def upgrade() -> None:
    bind = op.get_bind()
    for f in FILES:
        run_script(bind, read_sql(f))


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS ops CASCADE")
    op.execute("DROP ROLE IF EXISTS ops_analyst")
    op.execute("DROP ROLE IF EXISTS ops_finance_admin")
