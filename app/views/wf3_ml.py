import altair as alt
import pandas as pd
import streamlit as st

from app import common as c


def render() -> None:
    c.page_header("ML and forecasting", "A model ranks invoices by how unusual they look and says why; a separate forecast projects monthly spend. "
                  "Both are tested on later months than they were trained on.", "Workflow 3")
    a = c.ml_assets()
    sc = c.anomaly_scores()
    lab = c.labels().set_index("invoice_id")

    t1, t2, t3, t4 = st.tabs(["Anomaly ranking", "Review queue", "Drift", "Spend forecast"])
    with t1:
        m = a["metrics"][a["chosen"]]
        x, y, z, w = st.columns(4)
        x.metric("Model", a["chosen"])
        y.metric("PR-AUC (held-out months)", f"{m['pr_auc']:.2f}")
        z.metric("Recall in top 5%", f"{m['recall@5%']:.0%}")
        w.metric("Trained on / tested on", f"{a['n_train']:,} / {a['n_test']:,}")
        st.caption("Labels come from the same generator that planted the anomalies, so these scores show the pipeline works, "
                   "not how it would do on real invoices.")
        top = sc.sort_values("score", ascending=False).head(25).copy()
        top["reasons"] = top["reasons"].map(lambda r: "; ".join(r))
        top["known problem"] = [lab.loc[i, "anomaly_type"] if i in lab.index else "" for i in top.index]
        st.dataframe(top[["score", "flag", "reasons", "known problem"]].round({"score": 3}), width="stretch")
        imp = a["importances"].copy()
        imp.columns = ["feature", "weight"] + list(imp.columns[2:])
        imp = imp.sort_values("weight", ascending=False).head(10)
        st.markdown("**What the model pays attention to**")
        st.altair_chart(alt.Chart(imp).mark_bar(color=c.BLUE).encode(y=alt.Y("feature", sort="-x"), x="weight"), width="stretch")
    with t2:
        frac = st.slider("Share of invoices a person can review", 1, 20, 5) / 100
        n = max(1, int(len(sc) * frac))
        queue = sc.sort_values("score", ascending=False).head(n)
        truth = sc.index.isin(lab.index)
        hit = int(pd.Series(queue.index).isin(lab.index).sum())
        p, r = hit / n, hit / max(1, int(truth.sum()))
        x, y, z = st.columns(3)
        x.metric("Invoices to review", n)
        y.metric("Precision", f"{p:.0%}", help="Of the invoices in the queue, how many had a planted problem")
        z.metric("Recall", f"{r:.0%}", help="Of all planted problems, how many are in the queue")
        curve = []
        for f in range(1, 21):
            k = max(1, int(len(sc) * f / 100))
            h = int(pd.Series(sc.sort_values("score", ascending=False).head(k).index).isin(lab.index).sum())
            curve.append({"queue %": f, "precision": h / k, "recall": h / max(1, int(truth.sum()))})
        cv = pd.DataFrame(curve).melt("queue %", var_name="measure", value_name="value")
        st.altair_chart(alt.Chart(cv).mark_line(point=True).encode(x="queue %", y=alt.Y("value", axis=alt.Axis(format="%")), color="measure"), width="stretch")
    with t3:
        d = a["drift"].copy()
        st.dataframe(d, width="stretch")
        st.caption("Early history features (such as 'prior invoices from this supplier') always grow over time, so a high PSI there is expected rather than alarming. "
                   "PSI under 0.1 means little change, 0.1 to 0.25 means watch it, above 0.25 means retrain. Computed between the earlier and later months.")
    with t4:
        s, fc, bt = a["series"], a["forecast"], a["backtest"]
        hist = s.reset_index()
        hist.columns = ["month", "spend"]
        base = alt.Chart(hist).mark_line(color=c.BLUE).encode(x="month:T", y=alt.Y("spend:Q", title="Monthly spend"))
        band = alt.Chart(fc).mark_area(opacity=0.2, color=c.ORANGE).encode(x="month:T", y="lower:Q", y2="upper:Q")
        line = alt.Chart(fc).mark_line(color=c.ORANGE, strokeDash=[5, 3]).encode(x="month:T", y="forecast:Q")
        st.altair_chart(base + band + line, width="stretch")
        st.markdown("**How the forecast methods compared when replayed on past months**")
        st.dataframe(bt, width="stretch")
        st.caption("Lower sMAPE is better. A simple 'same as last year' method won, so that is what is used; a fancier model is not justified by this data.")