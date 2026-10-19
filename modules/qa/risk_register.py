"""Risk register with machine-checked traceability: every control must name a test that actually exists."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Risk:
    rid: str
    workflow: str
    risk: str
    likelihood: str       # low | medium | high (inherent, before controls)
    impact: str
    control: str
    verified_by: str      # tests/path.py::test_name
    residual: str
    owner: str


RISKS = [
    Risk("R01", "WF1", "OCR misreads an amount or invoice number", "high", "high", "Arithmetic validators + low-confidence routing to human review", "tests/unit/test_validators.py::test_total_mismatch_is_error", "medium", "AP lead"),
    Risk("R02", "WF2", "Duplicate or split invoice paid twice", "medium", "high", "SQL exception rules + payment block proposal", "tests/analytics/test_exceptions.py::test_sql_matches_pandas_oracle", "low", "Finance manager"),
    Risk("R03", "WF2", "Analyst sees suppliers outside their remit", "medium", "high", "Row-level security + security_invoker views", "tests/analytics/test_governance.py::test_views_respect_rls", "low", "Data owner"),
    Risk("R04", "WF2", "Audit trail altered after the fact", "low", "high", "Append-only trigger on audit_event", "tests/analytics/test_governance.py::test_audit_event_is_append_only", "low", "Security"),
    Risk("R05", "WF2", "Agent runs unbounded or destructive SQL", "medium", "high", "Named read-only KPI templates with validated parameters", "tests/analytics/test_kpis.py::test_kpi_execution_is_read_only", "low", "Data owner"),
    Risk("R06", "WF3", "Model degrades silently as data shifts", "medium", "medium", "PSI drift report + time-split evaluation + release gate", "tests/ml/test_ml.py::test_psi_properties", "medium", "ML owner"),
    Risk("R07", "WF3", "Features leak the future and inflate metrics", "medium", "high", "Expanding, shifted history features; leakage test", "tests/ml/test_ml.py::test_history_features_do_not_see_the_future", "low", "ML owner"),
    Risk("R08", "WF4", "Restricted policy text reaches an unauthorised user", "medium", "high", "Role-filtered index built before ranking", "tests/rag/test_rag.py::test_restricted_text_does_not_change_other_roles_index", "low", "Security"),
    Risk("R09", "WF4", "Assistant states something the policies do not say", "high", "high", "Extractive answers + grounding verification + abstention", "tests/rag/test_rag.py::test_tampered_answer_fails_grounding_check", "medium", "Policy owner"),
    Risk("R10", "WF4", "Poisoned or edited policy document is cited", "low", "high", "SHA-256 manifest required at load", "tests/qa/test_qa.py::test_poisoning_suite_detects_all_attacks", "low", "Policy owner"),
    Risk("R11", "WF5", "Hostile text in an invoice steers the agent", "medium", "high", "Deterministic decision policy; model writes prose only", "tests/agent/test_agent.py::test_prompt_injection_in_data_cannot_change_decision_or_actions", "low", "AI owner"),
    Risk("R12", "WF5", "Action taken without approval or by the requester", "medium", "high", "Approval gate with role authority and requester != approver", "tests/agent/test_agent.py::test_unauthorised_approvals_refused", "low", "Finance manager"),
    Risk("R13", "WF5", "Hijacked or hallucinating LLM output reaches users", "medium", "high", "Citation, number and language verification with template fallback", "tests/agent/test_agent.py::test_bad_llm_narratives_fall_back_to_template", "low", "AI owner"),
    Risk("R14", "WF6", "Retry causes a duplicate payment block", "medium", "high", "Idempotency keys reused across retries", "tests/erp/test_erp.py::test_retry_after_lost_response_does_not_double_block", "low", "Integration owner"),
    Risk("R15", "WF6", "Two writers overwrite each other", "low", "medium", "ETag / If-Match optimistic concurrency", "tests/erp/test_erp.py::test_status_change_bumps_etag_and_blocks_stale_writer", "low", "Integration owner"),
]


WORKFLOW_DIRS = {"WF1": "modules/ocr", "WF2": "modules/analytics", "WF3": "modules/ml", "WF4": "modules/rag", "WF5": "modules/agent", "WF6": "modules/erp"}


def untested_controls(root: Path = Path(".")) -> list[str]:
    """Risk ids whose ``verified_by`` test file or function does not exist.

    Risks for a workflow that is not installed in this checkout (its module directory is absent) are not applicable here
    and are skipped; in the full repository every workflow is present, so every risk is checked.
    """
    bad = []
    for r in RISKS:
        if r.workflow in WORKFLOW_DIRS and not (root / WORKFLOW_DIRS[r.workflow]).exists():
            continue
        path, _, name = r.verified_by.partition("::")
        f = root / path
        if not f.exists() or not re.search(rf"def {re.escape(name)}\b", f.read_text()):
            bad.append(r.rid)
    return bad


def to_markdown() -> str:
    head = "| ID | Workflow | Risk | Inherent (L/I) | Control | Verified by | Residual | Owner |\n|---|---|---|---|---|---|---|---|\n"
    rows = [f"| {r.rid} | {r.workflow} | {r.risk} | {r.likelihood}/{r.impact} | {r.control} | `{r.verified_by}` | {r.residual} | {r.owner} |" for r in RISKS]
    return "# Risk register\n\nGenerated from `modules/qa/risk_register.py`; `make gate` fails if any control points at a missing test.\n\n" + head + "\n".join(rows) + "\n"
