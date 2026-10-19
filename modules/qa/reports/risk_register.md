# Risk register

Generated from `modules/qa/risk_register.py`; `make gate` fails if any control points at a missing test.

| ID | Workflow | Risk | Inherent (L/I) | Control | Verified by | Residual | Owner |
|---|---|---|---|---|---|---|---|
| R01 | WF1 | OCR misreads an amount or invoice number | high/high | Arithmetic validators + low-confidence routing to human review | `tests/unit/test_validators.py::test_total_mismatch_is_error` | medium | AP lead |
| R02 | WF2 | Duplicate or split invoice paid twice | medium/high | SQL exception rules + payment block proposal | `tests/analytics/test_exceptions.py::test_sql_matches_pandas_oracle` | low | Finance manager |
| R03 | WF2 | Analyst sees suppliers outside their remit | medium/high | Row-level security + security_invoker views | `tests/analytics/test_governance.py::test_views_respect_rls` | low | Data owner |
| R04 | WF2 | Audit trail altered after the fact | low/high | Append-only trigger on audit_event | `tests/analytics/test_governance.py::test_audit_event_is_append_only` | low | Security |
| R05 | WF2 | Agent runs unbounded or destructive SQL | medium/high | Named read-only KPI templates with validated parameters | `tests/analytics/test_kpis.py::test_kpi_execution_is_read_only` | low | Data owner |
| R06 | WF3 | Model degrades silently as data shifts | medium/medium | PSI drift report + time-split evaluation + release gate | `tests/ml/test_ml.py::test_psi_properties` | medium | ML owner |
| R07 | WF3 | Features leak the future and inflate metrics | medium/high | Expanding, shifted history features; leakage test | `tests/ml/test_ml.py::test_history_features_do_not_see_the_future` | low | ML owner |
| R08 | WF4 | Restricted policy text reaches an unauthorised user | medium/high | Role-filtered index built before ranking | `tests/rag/test_rag.py::test_restricted_text_does_not_change_other_roles_index` | low | Security |
| R09 | WF4 | Assistant states something the policies do not say | high/high | Extractive answers + grounding verification + abstention | `tests/rag/test_rag.py::test_tampered_answer_fails_grounding_check` | medium | Policy owner |
| R10 | WF4 | Poisoned or edited policy document is cited | low/high | SHA-256 manifest required at load | `tests/qa/test_qa.py::test_poisoning_suite_detects_all_attacks` | low | Policy owner |
| R11 | WF5 | Hostile text in an invoice steers the agent | medium/high | Deterministic decision policy; model writes prose only | `tests/agent/test_agent.py::test_prompt_injection_in_data_cannot_change_decision_or_actions` | low | AI owner |
| R12 | WF5 | Action taken without approval or by the requester | medium/high | Approval gate with role authority and requester != approver | `tests/agent/test_agent.py::test_unauthorised_approvals_refused` | low | Finance manager |
| R13 | WF5 | Hijacked or hallucinating LLM output reaches users | medium/high | Citation, number and language verification with template fallback | `tests/agent/test_agent.py::test_bad_llm_narratives_fall_back_to_template` | low | AI owner |
| R14 | WF6 | Retry causes a duplicate payment block | medium/high | Idempotency keys reused across retries | `tests/erp/test_erp.py::test_retry_after_lost_response_does_not_double_block` | low | Integration owner |
| R15 | WF6 | Two writers overwrite each other | low/medium | ETag / If-Match optimistic concurrency | `tests/erp/test_erp.py::test_status_change_bumps_etag_and_blocks_stale_writer` | low | Integration owner |
