"""Bounded, stateful invoice-investigation workflow with human approval.

    run(invoice_id, requester, role)  ->  Case in AWAITING_APPROVAL / CLOSED_NO_ACTION / FAILED
    decide(case_id, approver, role, approve, comment)  ->  EXECUTED / REJECTED

Guarantees (each covered by a test): writes happen only after an authorised, different human approves; tool calls are
allow-listed, validated and budgeted; a failed tool becomes a documented gap, never a guess; text inside invoices or
documents cannot change the recommendation; narratives are verified against the evidence or replaced by a template.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from .llm import SYSTEM, LlmClient, evidence_block, render_template, verify_narrative
from .models import BudgetExceeded, Case, IllegalTransition, NotAuthorized, ProposedAction, State
from .policy import POLICY_QUESTION, assess
from .tools import INVOICE_ID, Backend, ToolRunner

ROLE_RANK = {"guest": 0, "analyst": 1, "finance_admin": 2}
MAX_POLICY_LOOKUPS = 3


class InvestigationWorkflow:
    def __init__(self, backend: Backend, llm: LlmClient | None = None, max_tool_calls: int = 12,
                 on_event: Callable[[dict[str, Any]], None] | None = None):
        self.backend, self.llm, self.max_tool_calls, self.on_event = backend, llm, max_tool_calls, on_event
        self.cases: dict[str, Case] = {}

    def _emit(self, case: Case, event: str, **kw: Any) -> None:
        if self.on_event:
            self.on_event({"case_id": case.case_id, "invoice_id": case.invoice_id, "event": event, "state": case.state.value, **kw})

    # ------------------------------------------------------------------ run
    def run(self, invoice_id: str, requested_by: str, role: str, question: str = "Should this invoice be paid?") -> Case:
        case = Case(f"CASE-{uuid.uuid4().hex[:8].upper()}", invoice_id, requested_by, role, question)
        self.cases[case.case_id] = case
        if role not in ROLE_RANK or ROLE_RANK[role] < ROLE_RANK["analyst"]:
            case.gaps.append("requester role may not investigate invoices")
            case.move(State.FAILED)
            return case
        if not re.match(INVOICE_ID, invoice_id or ""):
            case.gaps.append("invalid invoice id")
            case.move(State.FAILED)
            return case
        try:
            case.move(State.GATHER)
            self._gather(case)
            case.move(State.ASSESS)
            a = assess(case)
            case.recommendation, case.risk = a.recommendation, a.risk
            case.move(State.PROPOSE)
            self._narrate(case, a.reasons)
            if a.block_payment:
                key = hashlib.sha256(f"{case.case_id}:block:{invoice_id}".encode()).hexdigest()[:32]
                case.actions.append(ProposedAction("place_payment_block", invoice_id, "; ".join(a.reasons), key, a.authority))
                case.move(State.AWAITING_APPROVAL)
            else:
                case.move(State.CLOSED_NO_ACTION)
        except (BudgetExceeded, PermissionError, ValidationError) as exc:
            case.gaps.append(f"{type(exc).__name__}: {exc}")
            case.state = State.FAILED
            case.log("failed", error=type(exc).__name__)
        self._emit(case, "run_finished", recommendation=case.recommendation, risk=case.risk)
        return case

    def _gather(self, case: Case) -> None:
        tools = ToolRunner(self.backend, case, self.max_tool_calls)
        case.usage["steps"] += 1
        erp = tools.call("erp_invoice", {"invoice_id": case.invoice_id})
        if erp is None:
            raise PermissionError("invoice not found in ERP")
        recon = tools.call("reconciliation", {"invoice_id": case.invoice_id})
        tools.call("anomaly_score", {"invoice_id": case.invoice_id})
        if recon:
            codes = [e["code"] for e in sorted(recon.payload["exceptions"], key=lambda e: (e["severity"] != "error", e["code"]))]
            asked = set()
            for code in codes:
                q = POLICY_QUESTION.get(code)
                if q and q not in asked and len(asked) < MAX_POLICY_LOOKUPS:
                    asked.add(q)
                    tools.call("policy_search", {"question": q})

    def _narrate(self, case: Case, reasons: list[str]) -> None:
        template = render_template(case, reasons)
        case.narrative, case.narrative_source = template, "template"
        if self.llm is None:
            return
        try:
            text, tokens = self.llm.complete(SYSTEM, f"{evidence_block(case)}\nQuestion: {case.question}\nRecommendation: {case.recommendation}")
            case.usage["llm_calls"] += 1
            case.usage["llm_tokens"] += tokens
            ok, why = verify_narrative(text, case)
            case.log("narrative_verified" if ok else "narrative_rejected", reason=why)
            if ok:
                case.narrative, case.narrative_source = text, "llm"
        except Exception as exc:
            case.log("llm_failed", error=type(exc).__name__)

    # ------------------------------------------------------------------ human decision
    def decide(self, case_id: str, approver: str, role: str, approve: bool, comment: str = "") -> Case:
        case = self.cases[case_id]
        if case.state is not State.AWAITING_APPROVAL:
            raise IllegalTransition(f"case is {case.state.value}, not awaiting approval")
        if approver == case.requested_by:
            raise NotAuthorized("the requester cannot approve their own case")
        need = max(ROLE_RANK[a.requires_authority] for a in case.actions)
        if ROLE_RANK.get(role, -1) < need:
            raise NotAuthorized(f"role '{role}' lacks the authority required for this case")
        case.log("decision", approver=approver, approve=approve, comment=comment[:200])
        if not approve:
            for a in case.actions:
                a.status = "rejected"
            case.move(State.REJECTED)
            self._emit(case, "rejected", approver=approver)
            return case
        for a in case.actions:
            if a.status == "executed":
                continue
            a.result = self.backend.block_payment(a.invoice_id, a.reason[:480] or "case action", approver, a.idempotency_key)
            a.status = "executed"
            case.log("action_executed", action=a.action, key=a.idempotency_key)
        case.move(State.EXECUTED)
        self._emit(case, "executed", approver=approver)
        return case
