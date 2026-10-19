"""Deterministic recommendation policy. The language model has no say in this decision.

Why: the action a case leads to must be explainable, testable and immune to text inside invoices or documents.
The model only writes the explanation, afterwards, from the same evidence.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Case, Evidence

FRAUD_LIKE = {"DUPLICATE_INVOICE_NUMBER", "POSSIBLE_DUPLICATE", "BANK_ACCOUNT_CHANGED", "SPLIT_BELOW_THRESHOLD"}
POLICY_QUESTION = {
    "DUPLICATE_INVOICE_NUMBER": "What happens when two invoices from one supplier have the same number?",
    "POSSIBLE_DUPLICATE": "Invoices with the same total from one vendor within two weeks - what is the rule?",
    "SPLIT_BELOW_THRESHOLD": "What is invoice splitting and how do we detect it?",
    "BANK_ACCOUNT_CHANGED": "An invoice shows a different bank account than last time - what now?",
    "PRICE_VARIANCE": "What price difference is allowed between an invoice and the purchase order?",
    "NO_RECEIPT": "Can we pay for goods that have not arrived yet?",
    "QTY_OVER_RECEIPT": "Can we pay for goods that have not arrived yet?",
    "WEEKEND_POSTING": "Why is an invoice received on a Sunday flagged?",
    "ROUND_AMOUNT": "Are invoice totals that are multiples of one thousand a problem?",
    "NO_PO": "When is a purchase order required?",
}


@dataclass
class Assessment:
    recommendation: str      # hold | review | proceed
    risk: str                # low | medium | high | unknown
    reasons: list[str]
    block_payment: bool
    authority: str           # role needed to approve a block


def _first(case: Case, kind: str) -> Evidence | None:
    return next((e for e in case.evidence if e.kind == kind), None)


def assess(case: Case) -> Assessment:
    recon, anom, erp = _first(case, "reconciliation"), _first(case, "anomaly_score"), _first(case, "erp")
    reasons: list[str] = []
    if recon is None:
        return Assessment("review", "unknown", ["No reconciliation available, so the invoice cannot be cleared automatically."], False, "analyst")
    exc = recon.payload["exceptions"]
    errors = [e for e in exc if e["severity"] == "error"]
    warnings = [e for e in exc if e["severity"] == "warning"]
    codes = {e["code"] for e in exc}
    already_final = erp is not None and erp.payload.get("status") in {"paid", "rejected"}
    flagged = bool(anom and anom.payload.get("flag"))

    if errors:
        high = bool(codes & FRAUD_LIKE)
        reasons.append(f"{len(errors)} error-level exception(s): {', '.join(sorted(e['code'] for e in errors))}.")
        return Assessment("hold", "high" if high else "medium", reasons, not already_final, "finance_admin" if high else "analyst")
    if warnings or flagged:
        if warnings:
            reasons.append(f"Warning-level exception(s): {', '.join(sorted(e['code'] for e in warnings))}.")
        if flagged:
            reasons.append("The anomaly model flagged this invoice for review.")
        risk = "high" if codes & FRAUD_LIKE else "medium"
        return Assessment("review", risk, reasons, False, "analyst")
    if case.gaps:
        return Assessment("review", "unknown", ["Evidence is incomplete (see gaps), so the invoice is not cleared automatically."], False, "analyst")
    return Assessment("proceed", "low", ["No exceptions and no anomaly flag."], False, "analyst")
