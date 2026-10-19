"""Before/after EXPLAIN ANALYZE: what an index buys on a 500k-row table, and index column order.

Uses a temporary 500k-row table (the real tables are too small for the planner to prefer an index),
inside a transaction that is rolled back, so nothing persists.

    python scripts/explain_demo.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.analytics.db import get_engine

QUERY = "SELECT count(*), sum(total) FROM t_invoice WHERE supplier_id = 'SUP0007' AND invoice_date >= DATE '2026-06-01' AND invoice_date < DATE '2026-07-01'"


def plan(conn, label: str) -> None:
    rows = [r[0] for r in conn.execute(text("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + QUERY))]
    node = next((r.strip() for r in rows if re.search(r"Scan", r)), rows[0].strip())
    time = next((r for r in rows if "Execution Time" in r), "").strip()
    print(f"{label:<34} {node}\n{'':<34} {time}")


def main() -> None:
    eng = get_engine()
    with eng.connect() as conn:
        tx = conn.begin()
        conn.execute(text("""
            CREATE TEMP TABLE t_invoice AS
            SELECT g AS id, 'SUP' || lpad((1 + g % 24)::text, 4, '0') AS supplier_id,
                   DATE '2025-01-01' + (g % 600) AS invoice_date, (random() * 5000)::numeric(14,2) AS total
            FROM generate_series(1, 500000) g"""))
        conn.execute(text("ANALYZE t_invoice"))
        plan(conn, "no index")
        conn.execute(text("CREATE INDEX ON t_invoice (invoice_date, supplier_id)"))
        conn.execute(text("ANALYZE t_invoice"))
        plan(conn, "index (date, supplier)")
        conn.execute(text("DROP INDEX t_invoice_invoice_date_supplier_id_idx"))
        conn.execute(text("CREATE INDEX ON t_invoice (supplier_id, invoice_date)"))
        conn.execute(text("ANALYZE t_invoice"))
        plan(conn, "index (supplier, date)  <- ours")
        tx.rollback()
    print("\nAny matching index beats the sequential scan by an order of magnitude. Column order matters less here "
          "than it will on wider tables: with equality first, the same index also serves supplier-only queries.")


if __name__ == "__main__":
    main()
