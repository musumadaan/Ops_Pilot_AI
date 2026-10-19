"""Typed tools. The agent can call only what is registered here, with validated arguments, inside a call budget.

The model never writes SQL, never picks a URL and never calls an arbitrary function: it (or the deterministic plan)
selects a tool name and arguments, and the runner validates both.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

from .models import BudgetExceeded, Case, Evidence

INVOICE_ID = r"^[A-Za-z0-9_-]{3,40}$"


class InvoiceArgs(BaseModel):
    invoice_id: str = Field(pattern=INVOICE_ID)


class PolicyArgs(BaseModel):
    question: str = Field(min_length=3, max_length=300)


class KpiArgs(BaseModel):
    kpi_id: str = Field(pattern=r"^[a-z_]{3,60}$")
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    top_n: int | None = Field(default=None, ge=1, le=100)


class Backend(Protocol):
    """Everything the agent may know about the world. Live and fake implementations share this interface."""
    def erp_invoice(self, invoice_id: str) -> dict: ...
    def reconciliation(self, invoice_id: str) -> dict | None: ...
    def anomaly(self, invoice_id: str) -> dict | None: ...
    def policy(self, question: str, role: str) -> dict: ...
    def kpi(self, kpi_id: str, params: dict) -> dict: ...
    def block_payment(self, invoice_id: str, reason: str, requested_by: str, idempotency_key: str) -> dict: ...


class ToolRunner:
    """Executes registered tools for one case; validates args, enforces the allow-list and the call budget."""

    def __init__(self, backend: Backend, case: Case, max_calls: int = 12, allowed: set[str] | None = None):
        self.b, self.case, self.max_calls = backend, case, max_calls
        self.registry: dict[str, tuple[type[BaseModel], Callable[[BaseModel], Evidence]]] = {
            "erp_invoice": (InvoiceArgs, self._erp), "reconciliation": (InvoiceArgs, self._recon), "anomaly_score": (InvoiceArgs, self._anom),
            "policy_search": (PolicyArgs, self._policy), "kpi": (KpiArgs, self._kpi)}
        self.allowed = allowed if allowed is not None else set(self.registry)

    def call(self, name: str, args: dict[str, Any]) -> Evidence | None:
        """Returns Evidence, or None after recording a *gap* when the tool failed (recovery = degrade, never guess)."""
        if name not in self.registry or name not in self.allowed:
            self.case.log("tool_denied", tool=name)
            raise PermissionError(f"tool '{name}' is not available")
        if self.case.usage["tool_calls"] >= self.max_calls:
            raise BudgetExceeded(f"tool-call budget of {self.max_calls} exhausted")
        model, fn = self.registry[name]
        try:
            parsed = model(**args)
        except ValidationError as exc:
            self.case.log("tool_bad_args", tool=name, error=str(exc.errors()[0]["msg"]))
            raise
        self.case.usage["tool_calls"] += 1
        try:
            ev = fn(parsed)
        except Exception as exc:
            self.case.gaps.append(f"{name} unavailable: {type(exc).__name__}: {str(exc)[:120]}")
            self.case.log("tool_failed", tool=name, error=type(exc).__name__)
            return None
        if ev is None:
            self.case.gaps.append(f"{name}: no data returned")
            return None
        self.case.evidence.append(ev)
        self.case.log("tool_ok", tool=name, evidence=ev.evidence_id)
        return ev

    def _ev(self, kind: str, source: str, summary: str, payload: dict, prov: dict | None = None) -> Evidence:
        return Evidence(self.case.next_evidence_id(), kind, source, summary, payload, prov or {})

    def _erp(self, a: InvoiceArgs) -> Evidence:
        inv = self.b.erp_invoice(a.invoice_id)
        return self._ev("erp", "erp_invoice", f"ERP invoice {inv['invoice_id']}: total {inv['total']} {inv.get('currency', '')}, status {inv['status']}", inv)

    def _recon(self, a: InvoiceArgs) -> Evidence | None:
        r = self.b.reconciliation(a.invoice_id)
        if r is None:
            return None
        codes = [e["code"] for e in r["exceptions"]]
        return self._ev("reconciliation", "reconciliation", f"Match status {r['match_status']}; exceptions: {', '.join(codes) or 'none'}", r,
                        r.get("provenance", {}))

    def _anom(self, a: InvoiceArgs) -> Evidence | None:
        s = self.b.anomaly(a.invoice_id)
        if s is None:
            return None
        return self._ev("anomaly_score", "anomaly_score", f"Anomaly score {s['score']:.3f} ({'flagged' if s['flag'] else 'not flagged'})", s,
                        {"model_version": s.get("model_version")})

    def _policy(self, a: PolicyArgs) -> Evidence | None:
        r = self.b.policy(a.question, self.case.requester_role)
        if r["abstained"]:
            self.case.gaps.append(f"policy for '{a.question[:60]}' not found or not accessible to role {self.case.requester_role}")
            return None
        c = r["citations"][0]
        return self._ev("policy", "policy_search", f"{c['doc_id']} v{c['version']} / {c['section']}: {r['text']}", r,
                        {"doc_id": c["doc_id"], "version": c["version"], "section": c["section"]})

    def _kpi(self, a: KpiArgs) -> Evidence:
        params = {"start_date": a.start_date, "end_date": a.end_date}
        if a.top_n:
            params["top_n"] = a.top_n
        r = self.b.kpi(a.kpi_id, params)
        return self._ev("kpi", "kpi", f"KPI {a.kpi_id}: {len(r['rows'])} rows", r, r.get("provenance", {}))
