"""OpsPilot AI - interactive showcase.   streamlit run app/streamlit_app.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from app import common  # noqa: E402
from app.views import home, wf1_documents, wf2_data, wf3_ml, wf4_rag, wf5_agent, wf6_erp, wf7_qa, wf8_bi  # noqa: E402

st.set_page_config(page_title="OpsPilot AI", page_icon=":material/hub:", layout="wide")

nav = st.navigation({
    "Start": [st.Page(home.render, title="Overview", icon=":material/home:", url_path="overview", default=True)],
    "Workflows": [
        st.Page(wf1_documents.render, title="1 Document intelligence", icon=":material/description:", url_path="wf1"),
        st.Page(wf2_data.render, title="2 SQL and data engineering", icon=":material/database:", url_path="wf2"),
        st.Page(wf3_ml.render, title="3 ML and forecasting", icon=":material/query_stats:", url_path="wf3"),
        st.Page(wf4_rag.render, title="4 Policy retrieval", icon=":material/menu_book:", url_path="wf4"),
        st.Page(wf5_agent.render, title="5 Investigation agent", icon=":material/smart_toy:", url_path="wf5"),
        st.Page(wf6_erp.render, title="6 ERP integration", icon=":material/sync_alt:", url_path="wf6"),
        st.Page(wf7_qa.render, title="7 Safety and release gate", icon=":material/verified_user:", url_path="wf7"),
        st.Page(wf8_bi.render, title="8 Operations dashboard", icon=":material/dashboard:", url_path="wf8"),
    ],
})
with st.sidebar:
    st.caption("Synthetic data only. No real suppliers, invoices or payments.")
    st.caption(f"Data engine: **{common.engine_label()}**")
nav.run()