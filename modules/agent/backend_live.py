"""Backend that wires the agent to the real Workflow 2/3/4/6 components."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from sqlalchemy import Engine

from modules.analytics.kpi_service import run_kpi
from modules.analytics.reconcile import reconcile_invoice
from modules.erp.client import ErpClient
from modules.rag.answer import answer
from modules.rag.retriever import Retriever


class LiveBackend:
    def __init__(self, engine: Engine, erp_read: ErpClient, erp_write: ErpClient, retriever: Retriever,
                 anomaly_fn: Callable[[str], dict | None] | None = None):
        self.engine, self.erp_read, self.erp_write, self.retriever, self._anomaly = engine, erp_read, erp_write, retriever, anomaly_fn

    def erp_invoice(self, invoice_id: str) -> dict:
        return self.erp_read.get_invoice(invoice_id)

    def reconciliation(self, invoice_id: str) -> dict | None:
        r = reconcile_invoice(self.engine, invoice_id)
        return r.model_dump(mode="json") if r else None

    def anomaly(self, invoice_id: str) -> dict | None:
        return self._anomaly(invoice_id) if self._anomaly else None

    def policy(self, question: str, role: str) -> dict:
        return asdict(answer(self.retriever, question, role))

    def kpi(self, kpi_id: str, params: dict) -> dict:
        return run_kpi(self.engine, kpi_id, params).to_jsonable()

    def block_payment(self, invoice_id: str, reason: str, requested_by: str, idempotency_key: str) -> dict:
        return self.erp_write.place_payment_block(invoice_id, reason, requested_by, idempotency_key)


def make_anomaly_fn(bundle: dict, version: str, data_dir) -> Callable[[str], dict | None]:
    """Score every invoice once from the CSV history; returns a lookup. (Batch scoring stands in for a feature store.)"""
    from modules.ml.anomaly import score
    from modules.ml.features import build_features
    feats = build_features(data_dir)
    scored = score(bundle, feats)

    def lookup(invoice_id: str) -> dict | None:
        if invoice_id not in scored.index:
            return None
        r = scored.loc[invoice_id]
        return {"score": float(r["score"]), "flag": bool(r["flag"]), "reasons": list(r["reasons"]), "model_version": version}
    return lookup
