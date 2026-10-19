import pandas as pd
import streamlit as st

from app import common as c


def _case_view(case) -> None:
    from modules.agent.models import State
    kind = {"awaiting_approval": "warning", "executed": "ok", "closed_no_action": "ok", "rejected": "info", "failed": "error"}.get(case.state.value, "info")
    st.markdown(c.pill(case.state.value.replace("_", " "), kind) + f"**{case.case_id}** on {case.invoice_id}", unsafe_allow_html=True)
    a, b, d = st.columns(3)
    a.metric("Recommendation", case.recommendation or "-")
    b.metric("Risk", case.risk or "-")
    d.metric("Tool calls used", f"{case.usage['tool_calls']} of 12")
    if case.narrative:
        st.markdown(f'<div class="card">{case.narrative}<br><span class="muted">Written by: {case.narrative_source}</span></div>', unsafe_allow_html=True)
    if case.gaps:
        for g in case.gaps:
            st.markdown(c.pill("gap", "warning") + g, unsafe_allow_html=True)
    st.markdown("**Evidence the answer rests on**")
    for e in case.evidence:
        st.markdown(f'<div class="ev"><b>{e.evidence_id}</b> <span class="muted">{e.kind} via {e.source}</span><br>{e.summary}</div>', unsafe_allow_html=True)
    with st.expander("Step-by-step log"):
        st.dataframe(pd.DataFrame(case.history).drop(columns=["t"], errors="ignore"), hide_index=True, width="stretch")
    if case.actions:
        st.markdown("**Proposed action**")
        for act in case.actions:
            st.markdown(f"- `{act.action}` on **{act.invoice_id}** ({act.status}); needs authority **{act.requires_authority.replace('_', ' ')}**  \n  Reason: {act.reason}")
    if case.state is State.AWAITING_APPROVAL:
        _approval(case)


def _approval(case) -> None:
    from modules.agent.models import IllegalTransition, NotAuthorized
    wf = c.session_workflow()
    st.markdown("### Approval")
    st.caption("Nothing has been changed in the ERP yet. Try approving as the requester, or with a role that is too low, to see the refusals.")
    x, y = st.columns(2)
    who = x.text_input("Approver name", "maria.finance")
    role = y.selectbox("Approver role", ["analyst", "finance_admin", "guest"], index=1)
    note = st.text_input("Comment", "")
    b1, b2, _ = st.columns([1, 1, 3])
    for label, ok in (("Approve", True), ("Reject", False)):
        if (b1 if ok else b2).button(label, key=f"{label}-{case.case_id}"):
            try:
                wf.decide(case.case_id, who, role, ok, note)
                st.rerun()
            except NotAuthorized as e:
                st.markdown(c.pill("refused", "error") + str(e), unsafe_allow_html=True)
            except IllegalTransition as e:
                st.error(str(e))


def render() -> None:
    c.page_header("Investigation agent", "Pick an invoice and the agent gathers evidence (ERP record, rule results, anomaly score, policy, KPIs), "
                  "decides using fixed rules, and *proposes* an action. Only a different, authorised person can approve it. "
                  "The wording of the explanation is checked against the evidence before you see it.", "Workflow 5")
    exc = c.exceptions()
    flagged = sorted(exc["invoice_id"].unique())
    app = c.session_erp()[0]
    wf = c.session_workflow()
    x, y, z = st.columns(3)
    inv = x.selectbox("Invoice", flagged + ["INV-000001"], help="Flagged invoices first")
    who = y.text_input("Requested by", "ana.analyst")
    role = z.selectbox("Your role", ["analyst", "finance_admin", "guest"])
    unpaid = st.checkbox("Treat this invoice as not yet paid", value=True,
                         help="Most sample invoices are already paid, so there is nothing left to block. This sets its ERP status back to 'extracted'.")
    if st.button("Investigate", type="primary"):
        if unpaid:
            c.reset_invoice_status(app, inv, "extracted")
        st.session_state.case_id = wf.run(inv, who, role).case_id
    cid = st.session_state.get("case_id")
    if cid and cid in wf.cases:
        _case_view(wf.cases[cid])
    else:
        st.info("Choose an invoice and press Investigate.")
    if wf.cases:
        from modules.bi.telemetry import summarize
        with st.expander("Usage so far in this session"):
            st.json(summarize(wf.cases.values()))