import httpx
import pandas as pd
import streamlit as st

from app import common as c


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def render() -> None:
    from modules.erp.client import ErpClient, ErpError, ErpUnavailable
    c.page_header("ERP integration", "A small mock of an ERP system that speaks the same kind of web API real ones do (OData), plus a careful client. "
                  "Use it to see what happens when the ERP is down, when a request is sent twice, and when two people edit at once.", "Workflow 6")
    app, http, rd, wr = c.session_erp()
    if st.button("Reset the mock ERP"):
        st.session_state.pop("erp", None)
        st.session_state.pop("wf", None)
        st.rerun()
    t1, t2, t3, t4 = st.tabs(["Query", "Outage and retries", "Duplicate requests", "Edit conflicts"])
    with t1:
        ent = st.selectbox("Entity", ["Invoices", "PurchaseOrders", "Suppliers"])
        flt = st.text_input("$filter", "total gt 20000 and currency eq 'CAD'" if ent == "Invoices" else "")
        top = st.slider("$top", 1, 20, 5)
        r = http.get(f"/odata/{ent}", params={k: v for k, v in {"$filter": flt, "$top": top}.items() if v}, headers=_hdr("read-token"))
        if r.status_code == 200:
            st.dataframe(pd.DataFrame(r.json()["value"]), hide_index=True, width="stretch")
        else:
            st.markdown(c.pill(f"HTTP {r.status_code}", "error") + r.json().get("error", {}).get("message", r.text), unsafe_allow_html=True)
        st.markdown("**Try a hostile filter**")
        evil = st.selectbox("Example", ["total gt 1; DROP TABLE invoice", "1 eq 1 or total gt 0", "supplier_id eq 'x' UNION SELECT * FROM sqlite_master"])
        r2 = http.get("/odata/Invoices", params={"$filter": evil}, headers=_hdr("read-token"))
        st.markdown(c.pill(f"HTTP {r2.status_code}", "error" if r2.status_code >= 400 else "warning") + r2.json().get("error", {}).get("message", "accepted"), unsafe_allow_html=True)
        st.caption("Only a small, whitelisted filter language is understood. Anything else is rejected before it reaches the database.")
    with t2:
        n = st.slider("Make the next N calls fail", 0, 6, 2)
        code = st.selectbox("With status", [503, 500, 429], index=0)
        ra = st.checkbox("Server asks to wait 1s (Retry-After)", value=False)
        if st.button("Fetch INV-000001 now"):
            http.post("/_test/fail", params={"n": n, "status": code, **({"retry_after": 1} if ra else {})}, headers=_hdr("write-token"))
            slept: list[float] = []
            cl = ErpClient(http, "read-token", sleep=slept.append)
            try:
                inv = cl.get_invoice("INV-000001")
                st.markdown(c.pill("succeeded", "ok") + f"after {cl.attempts} attempt(s)", unsafe_allow_html=True)
                st.json({k: inv[k] for k in list(inv)[:6]})
            except ErpUnavailable as e:
                st.markdown(c.pill("gave up", "error") + f"after {cl.attempts} attempts: {e}", unsafe_allow_html=True)
            except ErpError as e:
                st.error(str(e))
            if slept:
                st.write("Waits between attempts (seconds, not actually slept in this demo):", [round(x, 2) for x in slept])
            http.post("/_test/fail", params={"n": 0}, headers=_hdr("write-token"))
        st.caption("The client waits longer after each failure, with random jitter, obeys Retry-After, and stops after 4 retries.")
    with t3:
        inv_id = st.text_input("Invoice to block", "INV-000010")
        key = st.text_input("Idempotency key", "demo-key-1")
        c1, c2 = st.columns(2)
        if c1.button("Send the request"):
            try:
                st.json(wr.place_payment_block(inv_id, "Demo block", "demo.user", key))
            except ErpError as e:
                st.error(str(e))
        if c2.button("Send it again (same key)"):
            try:
                st.json(wr.place_payment_block(inv_id, "Demo block", "demo.user", key))
            except ErpError as e:
                st.error(str(e))
        blocks = http.get("/odata/PaymentBlocks", params={"invoice_id": inv_id}, headers=_hdr("read-token")).json()["value"]
        st.write(f"Blocks on {inv_id}: **{len(blocks)}**")
        st.dataframe(pd.DataFrame(blocks), hide_index=True, width="stretch")
        st.caption("Sending the same request twice (for example after a lost response) returns the original result and still leaves exactly one block.")
    with t4:
        inv_id = st.text_input("Invoice", "INV-000020", key="conc")
        if st.button("Reset it to 'extracted', then person A and B both read it and both try to change it"):
            try:
                c.reset_invoice_status(app, inv_id, "extracted")
                got = http.get(f"/odata/Invoices/{inv_id}", headers=_hdr("read-token"))
                etag = got.headers["ETag"]
                st.write(f"Both read status **{got.json()['status']}** with version tag `{etag}`")
                a = http.patch(f"/odata/Invoices/{inv_id}", json={"status": "matched"}, headers={**_hdr("write-token"), "If-Match": etag})
                st.markdown(c.pill(f"A: HTTP {a.status_code}", "ok" if a.status_code < 300 else "error") + str(a.json()), unsafe_allow_html=True)
                b = http.patch(f"/odata/Invoices/{inv_id}", json={"status": "matched"}, headers={**_hdr("write-token"), "If-Match": etag})
                st.markdown(c.pill(f"B: HTTP {b.status_code}", "ok" if b.status_code < 300 else "error") + str(b.json().get("error", b.json())), unsafe_allow_html=True)
            except (KeyError, httpx.HTTPError) as e:
                st.error(f"Could not run: {e}")
        st.caption("B is refused (412) because A already changed the record. B must re-read before trying again, so nobody silently overwrites anyone.")