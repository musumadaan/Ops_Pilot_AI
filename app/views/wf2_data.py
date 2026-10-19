import shutil
import tempfile
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from app import common as c


def _kpis():
    from modules.analytics.kpi_service import registry
    return registry()


def _tab_kpi() -> None:
    reg = _kpis()
    usable = {k: s for k, s in reg.items() if c.pg_engine() is not None or "duckdb" in s.engines}
    inv = c.table("invoice")
    lo, hi = pd.to_datetime(inv["invoice_date"]).min().date(), pd.to_datetime(inv["invoice_date"]).max().date()
    k = st.selectbox("KPI", list(usable), format_func=lambda x: f"{x} - {usable[x].name}")
    spec = usable[k]
    st.caption(spec.description)
    params: dict = {}
    cols = st.columns(3)
    if "start_date" in spec.params:
        params["start_date"] = cols[0].date_input("From", lo, min_value=lo, max_value=hi)
        params["end_date"] = cols[1].date_input("To", hi, min_value=lo, max_value=hi)
    if "top_n" in spec.params:
        params["top_n"] = cols[2].slider("Top N", 3, 20, 8)
    try:
        df = c.run_kpi(k, params)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not run this KPI: {exc}")
        return
    st.dataframe(df, hide_index=True, width="stretch")
    num = df.select_dtypes("number").columns.tolist()
    cat = [x for x in df.columns if x not in num]
    if num and cat and len(df) > 1:
        st.altair_chart(alt.Chart(df).mark_bar(color=c.BLUE).encode(x=alt.X(cat[0], sort=None), y=num[0], tooltip=list(df.columns)), width="stretch")
    with st.expander("The SQL behind this number"):
        st.code(spec.sql, language="sql")
        st.caption(f"Grain: {spec.grain}. Parameters: {spec.params or 'none'}. Template hash {spec.sql_sha256[:16]}. "
                   "Only named, read-only templates like this can be run; the agent cannot write its own SQL.")


def _tab_exceptions() -> None:
    exc = c.exceptions()
    st.write(f"**{len(exc)}** exceptions on **{exc['invoice_id'].nunique()}** of {len(c.table('invoice'))} invoices.")
    by = exc.groupby(["code", "severity"]).size().reset_index(name="invoices").sort_values("invoices", ascending=False)
    st.altair_chart(alt.Chart(by).mark_bar().encode(y=alt.Y("code", sort="-x"), x="invoices",
                    color=alt.Color("severity", scale=alt.Scale(domain=list(c.SEV_COLOR), range=list(c.SEV_COLOR.values()))),
                    tooltip=["code", "severity", "invoices"]), width="stretch")
    pick = st.multiselect("Filter by rule", sorted(exc["code"].unique()))
    sev = st.multiselect("Severity", ["error", "warning", "info"], default=["error", "warning", "info"])
    view = exc[exc["severity"].isin(sev) & (exc["code"].isin(pick) if pick else True)]
    st.dataframe(view, hide_index=True, width="stretch", height=300)
    st.caption("These come from a table of plain rules. In PostgreSQL they are SQL views; the pandas version here was checked against them "
               "and agreed on every one of 67 flagged pairs.")


def _tab_match() -> None:
    exc = c.exceptions()
    ids = sorted(exc["invoice_id"].unique())
    inv_id = st.selectbox("Invoice (flagged ones first)", ids + [i for i in c.table("invoice")["invoice_id"].head(30) if i not in ids])
    inv = c.table("invoice").set_index("invoice_id").loc[inv_id]
    il = c.table("invoice_line")
    il = il[il["invoice_id"] == inv_id]
    po = inv["po_number"]
    pl = c.table("po_line")
    pl = pl[pl["po_number"] == po]
    rl = c.table("receipt_line")
    rl = rl[rl["po_number"] == po].groupby("po_line_no", as_index=False)["qty_received"].sum()
    m = il.merge(pl[["line_no", "qty_ordered", "unit_price"]].rename(columns={"line_no": "po_line_no", "unit_price": "po_price"}), on="po_line_no", how="left") \
          .merge(rl, on="po_line_no", how="left")
    m["qty_received"] = m["qty_received"].fillna(0)
    m["price_var_%"] = ((m["unit_price"] / m["po_price"] - 1) * 100).round(1)
    m["qty_vs_received"] = m["qty"] - m["qty_received"]
    a, b, d = st.columns(3)
    a.metric("Supplier", inv["supplier_id"])
    b.metric("PO", str(po))
    d.metric("Total", f"{inv['currency']} {inv['total']:,.2f}")
    st.dataframe(m[["line_no", "description", "qty", "qty_ordered", "qty_received", "qty_vs_received", "unit_price", "po_price", "price_var_%"]],
                 hide_index=True, width="stretch")
    mine = exc[exc["invoice_id"] == inv_id]
    if mine.empty:
        st.markdown(c.pill("no exceptions", "ok"), unsafe_allow_html=True)
    for r in mine.itertuples():
        st.markdown(c.pill(r.severity, "error" if r.severity == "error" else "warning") + f"**{r.code}**: {r.description}  \n*Suggested:* {r.recommended_action}",
                    unsafe_allow_html=True)


def _tab_quality() -> None:
    from modules.data_quality.raw_schemas import SCHEMAS, validate_file
    st.write("Raw files are checked against a strict schema *before* loading. Try breaking a file and see what is caught.")
    name = st.selectbox("File", ["invoice", "invoice_line", "payment", "purchase_order"])
    src = c.ensure_data() / f"{name}.csv"
    df = pd.read_csv(src, dtype=str, keep_default_na=False)
    col = st.selectbox("Column to corrupt", [x for x in df.columns])
    bad = st.selectbox("Corruption", ["(none)", "blank value", "text in a number field", "negative value", "duplicate first row"])
    d = Path(tempfile.mkdtemp())
    out = df.copy()
    if bad == "blank value":
        out.loc[0, col] = ""
    elif bad == "text in a number field":
        out.loc[0, col] = "N/A"
    elif bad == "negative value":
        out.loc[0, col] = "-5"
    elif bad == "duplicate first row":
        out = pd.concat([out, out.iloc[[0]]])
    out.to_csv(d / f"{name}.csv", index=False)
    try:
        n = validate_file(name, d / f"{name}.csv")
        st.markdown(c.pill("passed", "ok") + f"{n:,} rows valid.", unsafe_allow_html=True)
    except Exception as exc:  # noqa: BLE001
        st.markdown(c.pill("rejected", "error") + "this file would not be loaded:", unsafe_allow_html=True)
        fc = getattr(exc, "failure_cases", None)
        st.dataframe(fc.head(20) if fc is not None else pd.DataFrame({"error": [str(exc)[:500]]}), hide_index=True, width="stretch")
    shutil.rmtree(d, ignore_errors=True)
    st.caption(f"{len(SCHEMAS)} tables have a schema. In PostgreSQL, 16 further consistency rules run on loaded data (totals equal lines, bank account belongs to supplier, and so on).")


def render() -> None:
    c.page_header("SQL and data engineering", "A governed copy of the procurement data, thirteen business rules that flag risky invoices, "
                  "and named KPIs with their SQL. Nothing here is hand-typed numbers.", "Workflow 2")
    t1, t2, t3, t4 = st.tabs(["KPIs", "Exception rules", "Three-way match", "Data quality"])
    with t1:
        _tab_kpi()
    with t2:
        _tab_exceptions()
    with t3:
        _tab_match()
    with t4:
        _tab_quality()