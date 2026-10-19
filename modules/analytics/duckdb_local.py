"""Run the portable KPI templates on DuckDB over the generated CSVs - no server needed.

Useful on a laptop without Postgres, as a cross-engine check of the SQL, and for fast ad-hoc analysis.
Only KPIs whose header declares ``engines: ... duckdb`` are allowed; others use Postgres-only features.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import duckdb

from .kpi_service import KpiParamError, KpiResult, registry, validate_params

TABLES = ["supplier", "supplier_bank_account", "employee", "contract", "purchase_order", "po_line",
          "goods_receipt", "receipt_line", "invoice", "invoice_line", "payment", "budget"]
_TS = {"invoice": ("received_at", "approved_at")}


def connect(csv_dir: str | Path) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with an ``ops`` schema loaded from the CSV directory (all columns typed by sniffing)."""
    csv_dir = Path(csv_dir)
    con = duckdb.connect(":memory:")
    con.execute("SET TimeZone='UTC'")
    con.execute("CREATE SCHEMA ops")
    for t in TABLES:
        f = csv_dir / f"{t}.csv"
        if not f.exists():
            continue
        con.execute(f"CREATE TABLE ops.{t} AS SELECT * FROM read_csv('{f}', header=true, sample_size=-1)")
    return con


def _to_duckdb(sql: str) -> str:
    """':name' -> '$name' (DuckDB named parameters). Skips '::' casts."""
    return re.sub(r"(?<!:):(\w+)", r"$\1", sql)


def run_kpi(con: duckdb.DuckDBPyConnection, kpi_id: str, params: dict[str, Any] | None = None) -> KpiResult:
    spec = registry().get(kpi_id)
    if spec is None:
        raise KpiParamError(f"unknown KPI '{kpi_id}'")
    if "duckdb" not in spec.engines:
        raise KpiParamError(f"KPI '{kpi_id}' is not portable to DuckDB (engines: {list(spec.engines)})")
    bound = validate_params(spec, params or {})
    cur = con.execute(_to_duckdb(spec.sql), bound)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return KpiResult(kpi_id, cols, rows, {"kpi_id": kpi_id, "sql_sha256": spec.sql_sha256,
                                          "params": {k: str(v) for k, v in bound.items()},
                                          "row_count": len(rows), "engine": "duckdb"})
