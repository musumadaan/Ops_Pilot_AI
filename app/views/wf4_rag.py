import pandas as pd
import streamlit as st

from app import common as c


@st.cache_data(show_spinner="Running the evaluation...")
def _eval() -> dict:
    from modules.rag.evaluate import run
    return run(c.POLICIES)


def render() -> None:
    from modules.rag.answer import answer
    from modules.rag.corpus import GOLDEN, ROLE_ACCESS, UNANSWERABLE
    from modules.rag.retriever import MODES
    c.page_header("Policy retrieval", "Ask a question about company policy. The answer is copied from the policy text, shows exactly where it came from, "
                  "and the assistant says so when the policies do not cover the question. What you can see depends on your role.", "Workflow 4")
    r = c.retriever()
    t1, t2, t3 = st.tabs(["Ask", "Who can see what", "How well does it work"])
    with t1:
        role = st.radio("You are a", list(ROLE_ACCESS), index=1, horizontal=True, format_func=lambda x: x.replace("_", " "))
        samples = sorted({g[0] for g in GOLDEN})[:8] + UNANSWERABLE[:2] + ["A supplier sends new bank details. How do we verify them?"]
        pick = st.selectbox("Try a sample", ["(type your own)"] + samples)
        q = st.text_input("Question", value="" if pick == "(type your own)" else pick)
        mode = st.selectbox("Search method", MODES, index=2, help="hybrid combines keyword and meaning search; this is the default")
        if q:
            ans = answer(r, q, role, mode=mode)
            if ans.abstained:
                st.markdown(c.pill("not found", "warning") + ans.text, unsafe_allow_html=True)
                st.caption(f"Reason: {ans.reason}. Saying 'I don't know' is deliberate; guessing would be worse.")
            else:
                st.markdown(f'<div class="card">{ans.text}</div>', unsafe_allow_html=True)
                for ct in ans.citations:
                    st.markdown(f'<div class="cite"><b>{ct.title}</b> v{ct.version} - {ct.section} <span class="muted">({ct.doc_id}, characters {ct.start}-{ct.end})</span><br><i>"{ct.quote}"</i></div>',
                                unsafe_allow_html=True)
            with st.expander("Passages considered"):
                hits = r.search(q, role, k=5, mode=mode)
                st.dataframe(pd.DataFrame([{"rank": i + 1, "doc": h.chunk.doc_id, "section": h.chunk.section, "access": h.chunk.access,
                                            "score": round(h.score, 4)} for i, h in enumerate(hits)]), hide_index=True, width="stretch")
    with t2:
        probe = st.text_input("Probe question", "A supplier sends new bank details. How do we verify them?")
        rows = []
        for role in ROLE_ACCESS:
            a = answer(r, probe, role)
            hits = r.search(probe, role, k=5, mode="hybrid")
            rows.append({"role": role, "sees": ", ".join(sorted(ROLE_ACCESS[role])), "answer": "(abstained)" if a.abstained else a.text[:120],
                         "restricted passages returned": sum(h.chunk.access == "finance_restricted" for h in hits)})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Restricted text is removed before ranking, not hidden afterwards, so it cannot influence what other roles see.")
    with t3:
        ev = _eval()
        ab = pd.DataFrame(ev["ablation"]).T.round(3)
        st.markdown("**Does the right passage come back?** (13 golden questions)")
        st.dataframe(ab, width="stretch")
        st.markdown("**Answer quality**")
        st.json(ev["answers"])
        st.caption("The extra 'rerank' step scored slightly worse than plain hybrid, so it is not the default. Citation correctness of about 73% is a "
                   "known weakness: the answer sentence is sometimes taken from a neighbouring passage.")