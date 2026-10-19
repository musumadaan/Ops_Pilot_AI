"""A programmable agent backend for adversarial and regression tests (no network, no database)."""
from __future__ import annotations


class ScriptedBackend:
    def __init__(self, exceptions=(("DUPLICATE_INVOICE_NUMBER", "error"),), status="extracted", supplier="Acme Ltd",
                 detail_suffix="", policy_text="Hold and compare with the original.", anomaly_flag=False):
        self.exceptions, self.status, self.supplier, self.detail_suffix = exceptions, status, supplier, detail_suffix
        self.policy_text, self.anomaly_flag = policy_text, anomaly_flag
        self.blocks: list[tuple] = []
        self.calls: list[str] = []

    def erp_invoice(self, invoice_id):
        self.calls.append("erp_invoice")
        return {"invoice_id": invoice_id, "total": 9701.06, "currency": "CAD", "status": self.status, "supplier_name": self.supplier,
                "invoice_number": self.supplier}

    def reconciliation(self, invoice_id):
        self.calls.append("reconciliation")
        ex = [{"code": c, "severity": s, "occurrences": 1, "detail": f"{c} found {self.detail_suffix}".strip(), "recommended_action": "review"}
              for c, s in self.exceptions]
        return {"invoice_id": invoice_id, "match_status": "exception" if any(s == "error" for _, s in self.exceptions) else "matched",
                "exceptions": ex, "lines": [], "provenance": {"source": "scripted"}}

    def anomaly(self, invoice_id):
        return {"score": 0.9 if self.anomaly_flag else 0.01, "flag": self.anomaly_flag, "reasons": [], "model_version": "t"}

    def policy(self, question, role):
        self.calls.append("policy")
        return {"abstained": False, "text": self.policy_text,
                "citations": [{"doc_id": "POL-X", "version": "1", "section": "S"}]}

    def kpi(self, kpi_id, params):
        return {"rows": [], "provenance": {}}

    def block_payment(self, invoice_id, reason, requested_by, idempotency_key):
        self.blocks.append((invoice_id, requested_by, idempotency_key))
        return {"block_id": len(self.blocks), "invoice_id": invoice_id}
