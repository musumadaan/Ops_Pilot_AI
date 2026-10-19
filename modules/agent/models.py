"""Typed state for an invoice investigation case."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class State(str, Enum):
    INTAKE = "intake"
    GATHER = "gather"
    ASSESS = "assess"
    PROPOSE = "propose"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTED = "executed"
    REJECTED = "rejected"
    CLOSED_NO_ACTION = "closed_no_action"
    FAILED = "failed"


TRANSITIONS: dict[State, set[State]] = {
    State.INTAKE: {State.GATHER, State.FAILED},
    State.GATHER: {State.ASSESS, State.FAILED},
    State.ASSESS: {State.PROPOSE, State.FAILED},
    State.PROPOSE: {State.AWAITING_APPROVAL, State.CLOSED_NO_ACTION, State.FAILED},
    State.AWAITING_APPROVAL: {State.EXECUTED, State.REJECTED, State.FAILED},
    State.EXECUTED: set(), State.REJECTED: set(), State.CLOSED_NO_ACTION: set(), State.FAILED: set(),
}


class IllegalTransition(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class NotAuthorized(Exception):
    pass


@dataclass
class Evidence:
    evidence_id: str                     # E1, E2, ... the only way the narrative may reference facts
    kind: str                            # erp | reconciliation | anomaly_score | policy | kpi
    source: str                          # which tool produced it
    summary: str                         # one line a human can read
    payload: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProposedAction:
    action: str                          # place_payment_block | none
    invoice_id: str
    reason: str
    idempotency_key: str
    requires_authority: str              # role needed to approve
    status: str = "proposed"             # proposed | executed | rejected
    result: dict[str, Any] | None = None


@dataclass
class Case:
    case_id: str
    invoice_id: str
    requested_by: str
    requester_role: str
    question: str
    state: State = State.INTAKE
    evidence: list[Evidence] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    recommendation: str = ""             # hold | review | proceed
    risk: str = ""
    narrative: str = ""
    narrative_source: str = ""           # llm | template (fallback after failed verification)
    actions: list[ProposedAction] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=lambda: {"tool_calls": 0, "llm_calls": 0, "llm_tokens": 0, "steps": 0})

    def log(self, event: str, **detail: Any) -> None:
        self.history.append({"t": datetime.now(UTC).isoformat(timespec="seconds"), "state": self.state.value, "event": event, **detail})

    def move(self, new: State) -> None:
        if new not in TRANSITIONS[self.state]:
            raise IllegalTransition(f"{self.state.value} -> {new.value}")
        self.log("transition", to=new.value)
        self.state = new

    def next_evidence_id(self) -> str:
        return f"E{len(self.evidence) + 1}"
