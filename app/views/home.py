import shutil

import streamlit as st

from app import common as c

ROWS = [
    ("1  Document intelligence", "Pick an invoice PDF, extract fields with confidence and evidence boxes, compare with the known answer."),
    ("2  SQL and data engineering", "KPIs, the 13 exception rules, a three-way match for one invoice, and data checks that catch a corrupted file."),
    ("3  ML and forecasting", "Rank invoices by anomaly score with reasons, test a review-queue size, check drift, see a spend forecast."),
    ("4  Policy retrieval", "Ask policy questions as different roles; see cited answers, refusals, and that restricted text never leaks."),
    ("5  Investigation agent", "Run a case on an invoice, read the evidence, then approve or reject the proposed payment block."),
    ("6  ERP integration", "Query the mock ERP, break it on purpose, and watch retries, idempotency and conflict protection work."),
    ("7  Safety and release gate", "Run the release gate, attack the agent with hostile text, check policy tampering, read the risk register."),
    ("8  Operations dashboard", "Management KPIs and an investigation queue with drill-down."),
]


def render() -> None:
    st.markdown(c.CSS, unsafe_allow_html=True)
    inv, exc = c.table("invoice"), c.exceptions()
    n, flagged = len(inv), exc["invoice_id"].nunique()
    errs = exc[exc["severity"] == "error"]["invoice_id"].nunique()
    st.markdown(f"""
    <div class="hero"><h1>OpsPilot AI</h1>
    <p>An accounts-payable assistant built as eight small workflows, each tested on its own. It reads invoices, checks them against orders and receipts,
    explains what looks wrong, and proposes a payment block that a person must approve.</p>
    <div class="flow">
      <div class="step"><b>{n:,}</b><span>invoices received</span></div>
      <div class="step good"><b>{n - flagged:,}</b><span>passed every rule</span></div>
      <div class="step"><b>{flagged - errs:,}</b><span>warnings only</span></div>
      <div class="step bad"><b>{errs:,}</b><span>have an error, need a person</span></div>
    </div></div>""", unsafe_allow_html=True)

    st.subheader("How the pieces fit")
    st.graphviz_chart("""
    digraph G { rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", fillcolor="#EEF2FB", color="#2B59C3", fontname="Helvetica", fontsize=11];
      edge [color="#6B7685"];
      docs [label="Invoice PDFs\\nand scans", fillcolor="#fff", color="#6B7685"]; wf1 [label="1 Documents"]; wf2 [label="2 Data + SQL"];
      wf3 [label="3 ML"]; wf4 [label="4 Policy"]; wf6 [label="6 ERP"]; wf5 [label="5 Agent", fillcolor="#FBF1E3", color="#C77A1E"];
      human [label="Human\\napproval", shape=ellipse, fillcolor="#E5F3EE", color="#1E7B5F"]; wf7 [label="7 Safety gate", fillcolor="#FBE9E6", color="#C23B2E"];
      wf8 [label="8 Dashboard"];
      docs -> wf1 -> wf2; wf2 -> wf3; wf2 -> wf5; wf3 -> wf5; wf4 -> wf5; wf6 -> wf5; wf5 -> human -> wf6; wf2 -> wf8; wf5 -> wf8; wf7 -> wf5 [style=dashed, label=" gates"]; }
    """, width="stretch")
    st.caption("The agent never writes to the ERP by itself. It proposes, a different authorised person approves, and only then is anything changed.")

    st.subheader("What each page lets you try")
    st.markdown('<div class="deflist">' + "".join(f'<div class="k">{k}</div><div class="v">{v}</div>' for k, v in ROWS) + "</div>", unsafe_allow_html=True)

    st.subheader("This session")
    a, b, d = st.columns(3)
    a.metric("Data engine", "PostgreSQL" if "Postgre" in c.engine_label().split()[0] else "Offline")
    b.metric("Tesseract OCR", "found" if shutil.which("tesseract") else "not installed")
    d.metric("Policy documents", len(list(c.POLICIES.glob("POL-*.md"))) if c.POLICIES.exists() else 0)