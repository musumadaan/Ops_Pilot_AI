"""Agent backend that needs no database: pandas rules + DuckDB KPIs + the in-process mock ERP + the RAG index.

Used by the Streamlit demo so the whole project can be shown on a laptop without PostgreSQL. The exception rules are the
pandas implementation that is parity-tested against the SQL views (see tests/analytics/test_exceptions.py).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from modules.analytics import duckdb_local
from modules.analytics.offline_rules import exceptions_frame
from modules.erp.client import ErpClient
from modules.rag.answer import answer
from modules.rag.retriever import Retriever


class OfflineBackend:
    def __init__(self, data_dir: Path, erp_read: ErpClient, erp_write: ErpClient, retriever: Retriever,
                 anomaly_fn: Callable[[str], dict | None] | None = None, exceptions: pd.DataFrame | None = None):
        self.data_dir = Path(data_dir)
        self.erp_read, self.erp_write, self.retriever, self._anomaly = erp_read, erp_write, retriever, anomaly_fn
        self.exc = exceptions if exceptions is not None else exceptions_frame(self.data_dir)
        self._duck = None
        self.supplier_override: str | None = None      # lets the demo plant hostile text in an ERP field

    def erp_invoice(self, invoice_id: str) -> dict:
        inv = self.erp_read.get_invoice(invoice_id)
        if self.supplier_override is not None:
            inv["supplier_name"] = self.supplier_override
        return inv

    def reconciliation(self, invoice_id: str) -> dict | None:
        rows = self.exc[self.exc["invoice_id"] == invoice_id]
        ex = [{"code": r.code, "severity": r.severity, "occurrences": 1, "detail": r.description,
               "recommended_action": r.recommended_action} for r in rows.itertuples()]
        order = {"error": 0, "warning": 1, "info": 2}
        ex.sort(key=lambda e: (order[e["severity"]], e["code"]))
        return {"invoice_id": invoice_id, "match_status": "exception" if any(e["severity"] == "error" for e in ex) else "matched",
                "exceptions": ex, "lines": [], "provenance": {"source": "pandas rules (parity-tested against SQL views)"}}

    def anomaly(self, invoice_id: str) -> dict | None:
        return self._anomaly(invoice_id) if self._anomaly else None

    def policy(self, question: str, role: str) -> dict:
        return asdict(answer(self.retriever, question, role))

    def kpi(self, kpi_id: str, params: dict) -> dict:
        if self._duck is None:
            self._duck = duckdb_local.connect(self.data_dir)
        return duckdb_local.run_kpi(self._duck, kpi_id, params).to_jsonable()

    def block_payment(self, invoice_id: str, reason: str, requested_by: str, idempotency_key: str) -> dict:
        return self.erp_write.place_payment_block(invoice_id, reason, requested_by, idempotency_key)