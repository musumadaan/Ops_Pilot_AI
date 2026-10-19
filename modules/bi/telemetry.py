"""Workflow-outcome metrics from agent cases: automation, human override, cost, failures."""
from __future__ import annotations

from collections.abc import Iterable

from modules.agent.models import Case, State

PRICE_PER_1K_TOKENS = 0.0          # template narratives cost nothing; set when a paid model is configured


def summarize(cases: Iterable[Case], price_per_1k_tokens: float = PRICE_PER_1K_TOKENS) -> dict:
    cs = list(cases)
    n = len(cs)
    if n == 0:
        return {"cases": 0}
    by_state = {s.value: sum(c.state is s for c in cs) for s in State}
    decided = by_state["executed"] + by_state["rejected"]
    tokens = sum(c.usage["llm_tokens"] for c in cs)
    return {
        "cases": n,
        "by_state": by_state,
        "auto_cleared_rate": sum(c.state is State.CLOSED_NO_ACTION and c.recommendation == "proceed" for c in cs) / n,
        "needs_human_rate": sum(c.state in {State.AWAITING_APPROVAL, State.EXECUTED, State.REJECTED} or c.recommendation == "review" for c in cs) / n,
        "override_rate": (by_state["rejected"] / decided) if decided else None,
        "failure_rate": by_state["failed"] / n,
        "avg_tool_calls": sum(c.usage["tool_calls"] for c in cs) / n,
        "llm_tokens": tokens,
        "cost_per_case": tokens / 1000 * price_per_1k_tokens / n,
        "avg_gaps": sum(len(c.gaps) for c in cs) / n,
    }
