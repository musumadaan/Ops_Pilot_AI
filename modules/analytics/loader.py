"""Validate then bulk-load the synthetic CSV files into PostgreSQL with COPY.

    python scripts/load_synthetic.py --dir datasets/synthetic/procurement

Run it as the schema owner (the owner is exempt from the row-level-security policies via
``invoice_owner_all``). Existing business rows are replaced; reference tables
(``exception_code``, ``control_parameter``) are never touched.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from sqlalchemy import Engine, text

from modules.data_quality.raw_schemas import validate_file

LOAD_ORDER = [
    "supplier", "supplier_bank_account", "employee", "contract", "purchase_order", "po_line",
    "goods_receipt", "receipt_line", "invoice", "invoice_line", "payment", "budget", "user_supplier_access",
]


def _truncate(conn) -> None:
    conn.execute(text(
        "TRUNCATE " + ", ".join(f"ops.{t}" for t in reversed(LOAD_ORDER)) +
        ", ops.case_evidence, ops.approval, ops.investigation_case, ops.model_run CASCADE"))


def load_directory(engine: Engine, data_dir: str | Path, validate: bool = True, replace: bool = True) -> dict[str, int]:
    data_dir = Path(data_dir)
    counts: dict[str, int] = {}
    if validate:
        for name in LOAD_ORDER:
            validate_file(name, data_dir / f"{name}.csv")
    with engine.begin() as conn:
        if replace:
            _truncate(conn)
        cur = conn.connection.cursor()                      # psycopg 3 cursor on the same transaction
        for name in LOAD_ORDER:
            path = data_dir / f"{name}.csv"
            with open(path, newline="", encoding="utf-8") as fh:
                cols = next(csv.reader(fh))
            if name == "invoice":
                # COPY FROM cannot target a table with row-level security.
                # Stage the CSV, then INSERT into the protected invoice table.
                cur.execute(
                    "CREATE TEMP TABLE invoice_load "
                    "(LIKE ops.invoice INCLUDING DEFAULTS) ON COMMIT DROP"
                )
                sql = f"COPY invoice_load ({', '.join(cols)}) FROM STDIN WITH (FORMAT csv, HEADER true)"
                with cur.copy(sql) as copy, open(path, "rb") as fh:
                    while chunk := fh.read(1 << 20):
                        copy.write(chunk)

                conn.execute(text(
                    f"INSERT INTO ops.invoice ({', '.join(cols)}) "
                    f"SELECT {', '.join(cols)} FROM invoice_load"
                ))
            else:
                sql = f"COPY ops.{name} ({', '.join(cols)}) FROM STDIN WITH (FORMAT csv, HEADER true)"
                with cur.copy(sql) as copy, open(path, "rb") as fh:
                    while chunk := fh.read(1 << 20):
                        copy.write(chunk)

            counts[name] = cur.execute(f"SELECT count(*) FROM ops.{name}").fetchone()[0]
        conn.execute(text(
            "INSERT INTO ops.audit_event (actor, action, entity, detail) VALUES "
            "('loader', 'load_synthetic', 'ops', CAST(:d AS jsonb))"), {"d": json.dumps(counts)})
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("REFRESH MATERIALIZED VIEW ops.mv_monthly_spend"))
        conn.execute(text("ANALYZE"))
    return counts
