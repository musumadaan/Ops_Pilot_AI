import altair as alt
import pandas as pd
import streamlit as st

from app import common as c


def _safe_kpi(k: str, p: dict) -> pd.DataFrame:
    try:
        return c.run_kpi(k, p)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def render() -> None:
    c.page_header("Operations dashboard", "What a finance manager sees: headline numbers, trends, and a queue of invoices that need a person, with the reasons.", "Workflow 8")
    inv = c.table("invoice")
    d = pd.to_datetime(inv["invoice_date"])
    lo, hi = d.min().date(), d.max().date()
    a, b = st.columns(2)
    start = a.date_input("From", lo, min_value=lo, max_value=hi)
    end = b.date_input("To", hi, min_value=lo, max_value=hi)
    p = {"start_date": start, "end_date": end}
    mask = (d.dt.date >= start) & (d.dt.date <= end)
    exc = c.exceptions()
    in_range = inv[mask]
    flagged = exc[exc["invoice_id"].isin(in_range["invoice_id"])]
    cyc = _safe_kpi("invoice_cycle_time", p)
    pay = _safe_kpi("payment_timeliness", p)
    t = st.columns(4)
    t[0].metric("Invoices", f"{len(in_range):,}")
    t[1].metric("Spend", f"{in_range['total'].sum():,.0f}")
    t[2].metric("Need review", flagged["invoice_id"].nunique(), f"{flagged['invoice_id'].nunique() / max(1, len(in_range)):.1%} of invoices", delta_color="off")
    t[3].metric("Errors", int((flagged["severity"] == "error").sum()))

    l, r = st.columns(2)
    with l:
        st.markdown("**Top suppliers by spend**")
        top = _safe_kpi("top_suppliers_by_spend", {**p, "top_n": 8})
        if not top.empty:
            num = top.select_dtypes("number").columns[0]
            lab = [x for x in top.columns if x not in top.select_dtypes("number").columns][0]
            st.altair_chart(alt.Chart(top).mark_bar(color=c.BLUE).encode(y=alt.Y(lab, sort="-x"), x=num, tooltip=list(top.columns)), width="stretch")
    with r:
        st.markdown("**Exceptions by rule**")
        by = flagged.groupby(["code", "severity"]).size().reset_index(name="n")
        if not by.empty:
            st.altair_chart(alt.Chart(by).mark_bar().encode(y=alt.Y("code", sort="-x"), x="n", color=alt.Color("severity", scale=alt.Scale(
                domain=list(c.SEV_COLOR), range=list(c.SEV_COLOR.values()))), tooltip=["code", "severity", "n"]), width="stretch")
    l2, r2 = st.columns(2)
    for col, df, title in ((l2, cyc, "Invoice cycle time by month"), (r2, pay, "Payment timeliness by month")):
        with col:
            st.markdown(f"**{title}**")
            if not df.empty:
                num = df.select_dtypes("number").columns[0]
                x = [k for k in df.columns if k not in df.select_dtypes("number").columns][0]
                st.altair_chart(alt.Chart(df).mark_line(point=True, color=c.ORANGE).encode(x=x, y=num, tooltip=list(df.columns)), width="stretch")

    st.markdown("### Investigation queue")
    sc = c.anomaly_scores()
    q = flagged.groupby("invoice_id").agg(rules=("code", lambda s: ", ".join(sorted(s))), worst=("severity", lambda s: "error" if "error" in set(s) else "warning" if "warning" in set(s) else "info"))
    q = q.join(inv.set_index("invoice_id")[["supplier_id", "total", "currency", "invoice_date"]]).join(sc[["score"]].round(3))
    sev = st.multiselect("Show", ["error", "warning", "info"], default=["error", "warning"])
    q = q[q["worst"].isin(sev)].sort_values(["worst", "score"], ascending=[True, False])
    sel = st.dataframe(q, width="stretch", height=300, on_select="rerun", selection_mode="single-row")
    rows = sel.selection.rows if sel and sel.selection else []
    if rows:
        iid = q.index[rows[0]]
        st.markdown(f"**{iid}**")
        for e in exc[exc["invoice_id"] == iid].itertuples():
            st.markdown(f'<div class="ev">{c.pill(e.severity, "error" if e.severity == "error" else "warning")}<b>{e.code}</b>: {e.description}<br><i>{e.recommended_action}</i></div>', unsafe_allow_html=True)
        look = c.anomaly_lookup()(iid)
        if look:
            st.write(f"Model score **{look['score']:.2f}**" + (": " + "; ".join(look["reasons"]) if look["reasons"] else ""))
        st.caption("To act on this invoice, open the Investigation agent page.")
    if c.pg_engine() is not None:
        with st.expander("Export a standalone HTML dashboard"):
            if st.button("Build"):
                from modules.bi.data import build_dataset
                from modules.bi.render import render as render_html
                html = render_html(build_dataset(c.pg_engine(), str(start), str(end)))
                st.download_button("Download dashboard.html", html, "dashboard.html", "text/html")