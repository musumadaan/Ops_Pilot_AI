"""Shared state for the Streamlit showcase: data, models, retrieval index, mock ERP, optional PostgreSQL."""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("ERP_TEST_HOOKS", "1")            # lets the ERP page inject failures; harmless for a local demo

CACHE = Path(os.getenv("OPSPILOT_CACHE", Path(tempfile.gettempdir()) / "opspilot_demo"))
DATA = ROOT / "datasets/synthetic/procurement"
POLICIES = ROOT / "datasets/synthetic/policies"
SUPPLIERS = ROOT / "datasets/synthetic/suppliers.csv"
BLUE, ORANGE, RED, AMBER, GREY, GREEN = "#2B59C3", "#C77A1E", "#C23B2E", "#B7791F", "#6B7685", "#1E7B5F"
SEV_COLOR = {"error": RED, "warning": AMBER, "info": GREY}
ROLES = ["guest", "analyst", "finance_admin"]
WF_NAMES = ["Documents", "Data", "ML", "Policy", "Agent", "ERP", "Safety", "Dashboard"]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400;500;600;700&display=swap');
:root{--ink:#0E1A2B;--paper:#F7F8FA;--rule:#D9DEE5;--blue:#2B59C3;--red:#C23B2E;--amber:#B7791F;--green:#1E7B5F;--grey:#6B7685}
[data-testid="stAppViewContainer"],[data-testid="stSidebar"],[data-testid="stMain"] h1,[data-testid="stMain"] h2,[data-testid="stMain"] h3,[data-testid="stMain"] h4{font-family:'Schibsted Grotesk',system-ui,sans-serif}
.block-container{padding-top:2rem;max-width:1180px}
h1{font-weight:700;letter-spacing:-.02em;font-size:2.1rem!important;margin-bottom:.1rem}
h2,h3{font-weight:650;letter-spacing:-.01em}
[data-testid="stMain"] p,[data-testid="stMain"] li{line-height:1.55}
.lede{max-width:64ch;color:#3A475A;font-size:1.02rem;margin:.2rem 0 1.2rem}
.rail{display:flex;gap:4px;margin:0 0 .6rem;max-width:520px}
.rail span{flex:1;height:4px;border-radius:2px;background:var(--rule)}
.rail span.on{background:var(--blue)}.rail span.done{background:#9DB3E4}
.railtxt{font-size:.8rem;color:var(--grey);margin-bottom:.35rem}
.pill{display:inline-block;padding:1px 8px;border-radius:3px;font-size:.74rem;font-weight:650;border:1.5px solid;margin-right:6px;letter-spacing:.01em}
.pill.error{color:var(--red)}.pill.warning{color:var(--amber)}.pill.info{color:var(--grey)}.pill.ok{color:var(--green)}.pill.blue{color:var(--blue)}
.card{border:1px solid var(--rule);background:#fff;border-radius:8px;padding:12px 16px;margin:6px 0 10px}
.card h4{margin:0 0 4px 0;font-size:.95rem}.muted{color:var(--grey);font-size:.84rem}
.cite{border-left:3px solid var(--blue);padding:6px 12px;margin:8px 0;background:#EEF2FB;border-radius:0 6px 6px 0;font-size:.9rem}
.ev{border-left:3px solid var(--ink);padding:4px 12px;margin:6px 0;font-size:.9rem;background:#fff}
[data-testid="stMetric"]{background:#fff;border:1px solid var(--rule);border-left:3px solid var(--ink);border-radius:6px;padding:10px 14px}
[data-testid="stMetricValue"]{font-size:1.55rem;font-weight:650;font-variant-numeric:tabular-nums}
[data-testid="stMetricLabel"] p{color:var(--grey);font-size:.82rem}
.stTabs [data-baseweb="tab-list"]{gap:4px;border-bottom:1px solid var(--rule)}
.stTabs [data-baseweb="tab"]{height:2.5rem;padding:0 14px;font-weight:550}
.stTabs [aria-selected="true"]{color:var(--blue)}
.stButton>button{border-radius:6px;font-weight:600}
[data-testid="stDataFrame"]{border:1px solid var(--rule);border-radius:6px}
[data-testid="stSidebar"] [data-testid="stSidebarNav"] a{border-radius:6px}
.hero{background:var(--ink);color:#EAF0FA;border-radius:10px;padding:26px 28px 22px;margin:0 0 20px}
.hero h1{color:#fff;font-size:2.4rem!important;margin:0 0 6px}
.hero p{color:#B9C5D9;max-width:62ch;margin:0 0 18px}
.flow{display:flex;flex-wrap:wrap;gap:0;align-items:stretch}
.step{flex:1 1 150px;padding:10px 14px;border-left:2px solid #34476A}
.step b{display:block;font-size:1.7rem;font-variant-numeric:tabular-nums;color:#fff;font-weight:650}
.step span{font-size:.82rem;color:#A9B7CF}
.step.bad{border-left-color:#E5604F}.step.bad b{color:#FF9C8F}
.step.good{border-left-color:#4BB58F}
.deflist{display:grid;grid-template-columns:minmax(190px,max-content) 1fr;gap:0 24px;border-top:1px solid var(--rule)}
.deflist div{padding:9px 0;border-bottom:1px solid var(--rule)}
.deflist .k{font-weight:650}.deflist .v{color:#3A475A}
@media (max-width:700px){.deflist{grid-template-columns:1fr}.deflist .k{border-bottom:0;padding-bottom:0}.step{flex-basis:45%}}
</style>
"""


def pill(text: str, kind: str = "info") -> str:
    return f'<span class="pill {kind}">{text}</span>'


def page_header(title: str, blurb: str, wf: str | None = None) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    if wf:
        n = int(wf.split()[-1])
        segs = "".join(f'<span class="{"on" if k == n else "done" if k < n else ""}"></span>' for k in range(1, 9))
        st.markdown(f'<div class="rail">{segs}</div><div class="railtxt">Workflow {n} of 8</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<div class="lede">{blurb}</div>', unsafe_allow_html=True)


def ensure_data() -> Path:
    if not (DATA / "invoice.csv").exists():
        from modules.analytics.synthetic import Generator
        Generator(SUPPLIERS, 42).write(DATA)
    if not (POLICIES / "POL-AP-001.md").exists():
        from modules.rag.corpus import write_corpus
        from modules.rag.integrity import write_manifest
        write_corpus(POLICIES)
        write_manifest(POLICIES)
    return DATA


@st.cache_data(show_spinner=False)
def table(name: str) -> pd.DataFrame:
    return pd.read_csv(ensure_data() / f"{name}.csv")


@st.cache_data(show_spinner=False)
def exceptions() -> pd.DataFrame:
    from modules.analytics.offline_rules import exceptions_frame
    return exceptions_frame(ensure_data())


@st.cache_data(show_spinner=False)
def labels() -> pd.DataFrame:
    return pd.read_csv(ensure_data() / "labels/anomaly_labels.csv")


# ---------------------------------------------------------------------------- optional PostgreSQL
@st.cache_resource(show_spinner=False)
def pg_engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    try:
        from sqlalchemy import text

        from modules.analytics.db import get_engine
        eng = get_engine()
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM ops.invoice LIMIT 1"))
        return eng
    except Exception:  # noqa: BLE001 - any failure just means "offline mode"
        return None


def engine_label() -> str:
    return "PostgreSQL (live SQL views)" if pg_engine() is not None else "Offline (pandas rules + DuckDB)"


def run_kpi(kpi_id: str, params: dict[str, Any]) -> pd.DataFrame:
    from modules.analytics import duckdb_local
    from modules.analytics.kpi_service import registry
    from modules.analytics.kpi_service import run_kpi as pg_run
    wanted = registry()[kpi_id].params
    p = {k: v for k, v in params.items() if k in wanted}
    eng = pg_engine()
    if eng is not None:
        res = pg_run(eng, kpi_id, p)
    else:
        if "duckdb" not in registry()[kpi_id].engines:
            raise RuntimeError("this KPI needs PostgreSQL")
        res = duckdb_local.run_kpi(duckdb_local.connect(ensure_data()), kpi_id, p)
    df = pd.DataFrame(res.rows)
    for c in df.columns:
        if df[c].map(lambda v: hasattr(v, "quantize")).any():
            df[c] = df[c].astype(float)
    return df


# ---------------------------------------------------------------------------- retrieval / ML / ERP
@st.cache_resource(show_spinner="Indexing policies...")
def retriever():
    from modules.rag.documents import load_corpus
    from modules.rag.retriever import Retriever
    ensure_data()
    return Retriever(load_corpus(POLICIES))


@st.cache_resource(show_spinner="Training the anomaly model (a few seconds, once)...")
def ml_assets():
    """Train on a larger synthetic history, then score the demo dataset. Returns (bundle, version, metrics, drift, forecast parts)."""
    from modules.analytics.synthetic import Generator
    from modules.ml import registry
    from modules.ml.anomaly import time_split, train
    from modules.ml.drift import drift_report
    from modules.ml.features import FEATURES, build_features
    from modules.ml.forecast import backtest, forecast, monthly_spend
    big = CACHE / "ml_data"
    if not (big / "invoice.csv").exists():
        Generator(SUPPLIERS, 7, n_po=3000, per_type=12, start=date(2023, 10, 1)).write(big)
    df = build_features(big)
    res = train(df, root=CACHE / "registry")
    bundle, meta = registry.load("invoice_anomaly", root=CACHE / "registry")
    tr, te = time_split(df)
    series = monthly_spend(big).set_index("month")["subtotal"].asfreq("MS", fill_value=0.0).iloc[:-1]
    bt, _ = backtest(series)
    return {"bundle": bundle, "version": meta["version"], "metrics": res.metrics, "chosen": res.chosen, "importances": res.importances,
            "drift": drift_report(tr, te, FEATURES), "series": series, "backtest": bt, "forecast": forecast(series), "n_train": len(tr), "n_test": len(te)}


@st.cache_resource(show_spinner=False)
def anomaly_scores() -> pd.DataFrame:
    from modules.ml.anomaly import score
    from modules.ml.features import build_features
    a = ml_assets()
    feats = build_features(ensure_data())
    sc = score(a["bundle"], feats)
    return feats.join(sc)


def anomaly_lookup():
    sc = anomaly_scores()
    ver = ml_assets()["version"]

    def f(invoice_id: str) -> dict | None:
        if invoice_id not in sc.index:
            return None
        r = sc.loc[invoice_id]
        return {"score": float(r["score"]), "flag": bool(r["flag"]), "reasons": list(r["reasons"]), "model_version": ver}
    return f


def new_erp():
    """A fresh in-process mock ERP (own state) plus read/write clients."""
    from fastapi.testclient import TestClient

    from modules.erp.app import create_app
    from modules.erp.client import ErpClient
    app = create_app(str(ensure_data()))
    http = TestClient(app)
    return app, http, ErpClient(http, "read-token"), ErpClient(http, "write-token")


def session_erp():
    if "erp" not in st.session_state:
        st.session_state.erp = new_erp()
    return st.session_state.erp


def make_backend(erp=None):
    from modules.agent.backend_offline import OfflineBackend
    _, _, rd, wr = erp or session_erp()
    return OfflineBackend(ensure_data(), rd, wr, retriever(), anomaly_lookup(), exceptions())


def reset_invoice_status(app, invoice_id: str, status: str = "extracted") -> None:
    """Demo helper: put an invoice back to an earlier ERP status so the workflow has something to act on."""
    app.state.con.execute("UPDATE invoice SET status = ?, version = version + 1 WHERE invoice_id = ?", [status, invoice_id])
    app.state.con.commit()


def session_workflow():
    from modules.agent.workflow import InvestigationWorkflow
    if "wf" not in st.session_state:
        st.session_state.wf = InvestigationWorkflow(make_backend())
    return st.session_state.wf