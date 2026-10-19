"""SQLite-backed store for the mock ERP, seeded from the Workflow 2 synthetic CSVs."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

ENTITIES = {
    "Suppliers": ("supplier", "supplier_id", ["supplier_id", "name", "country", "payment_terms_days", "status"]),
    "PurchaseOrders": ("purchase_order", "po_number", ["po_number", "supplier_id", "buyer_id", "category", "currency", "order_date", "status"]),
    "GoodsReceipts": ("goods_receipt", "receipt_id", ["receipt_id", "po_number", "received_date"]),
    "Invoices": ("invoice", "invoice_id", ["invoice_id", "supplier_id", "invoice_number", "po_number", "invoice_date", "due_date",
                                           "currency", "subtotal", "tax", "total", "status"]),
}
CHILDREN = {"PurchaseOrders": ("po_line", "po_number", ["po_number", "line_no", "description", "qty_ordered", "unit_price"])}

SCHEMA = """
CREATE TABLE idempotency (key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, status INTEGER NOT NULL, body TEXT NOT NULL);
CREATE TABLE payment_block (block_id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT NOT NULL, reason TEXT NOT NULL,
                            created_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def seed(con: sqlite3.Connection, csv_dir: str | Path) -> dict[str, int]:
    csv_dir = Path(csv_dir)
    counts = {}
    for entity, (table, _, cols) in ENTITIES.items():
        df = pd.read_csv(csv_dir / f"{table}.csv")[[c for c in cols if c != "version"]]
        if entity == "Invoices":
            df["version"] = 1
        df.to_sql(table, con, index=False, if_exists="replace")
        counts[entity] = len(df)
    for _, (table, _, cols) in CHILDREN.items():
        pd.read_csv(csv_dir / f"{table}.csv")[cols].to_sql(table, con, index=False, if_exists="replace")
    con.executescript(SCHEMA)
    con.execute("CREATE INDEX IF NOT EXISTS invoice_po ON invoice(po_number)")
    con.commit()
    return counts
