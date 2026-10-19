import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from modules.analytics.synthetic import Generator
from modules.erp import odata
from modules.erp.app import create_app
from modules.erp.client import ErpClient, ErpError, ErpUnavailable

ROOT = Path(__file__).resolve().parents[2]
READ, WRITE = {"Authorization": "Bearer read-token"}, {"Authorization": "Bearer write-token"}


@pytest.fixture(scope="module")
def csv_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("erpdata")
    Generator(ROOT / "datasets/synthetic/suppliers.csv", 3, n_po=300, per_type=3).write(d)
    return d


@pytest.fixture()
def api(csv_dir, monkeypatch):
    monkeypatch.setenv("ERP_TEST_HOOKS", "1")
    app = create_app(str(csv_dir))
    return TestClient(app), app


def client(api, token="read-token", **kw):
    sleeps = []
    c = ErpClient(api[0], token, sleep=sleeps.append, **kw)
    return c, sleeps


# ---------------- OData subset
def test_filter_parsing_and_injection_safety():
    where, params = odata.parse_filter("status eq 'paid' and total ge 1000.5", {"status", "total"})
    assert where == "status = ? AND total >= ?" and params == ["paid", 1000.5]
    for bad in ["status eq 'x' or total gt 1", "bogus eq 1", "status eq paid", "status = 'x'", "1=1; DROP TABLE invoice"]:
        with pytest.raises(odata.ODataError):
            odata.parse_filter(bad, {"status", "total"})
    w, p = odata.parse_filter("status eq 'a''b'", {"status"})
    assert p == ["a'b"]


def test_build_limits():
    with pytest.raises(odata.ODataError):
        odata.build({"a"}, None, None, 10_000, 0, None, "a ASC")
    with pytest.raises(odata.ODataError):
        odata.build({"a"}, None, "a; DROP", None, 0, None, "a ASC")
    with pytest.raises(odata.ODataError):
        odata.build({"a"}, None, None, 5, 0, "zzz", "a ASC")


# ---------------- contract: reads
def test_auth_required_and_scoped(api):
    c, _ = api
    assert c.get("/odata/Invoices").status_code == 401
    assert c.get("/odata/Invoices", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/odata/Invoices", headers=READ).status_code == 200
    r = c.post("/odata/PaymentBlocks", json={"invoice_id": "INV-000001", "reason": "test", "requested_by": "u"},
               headers={**READ, "Idempotency-Key": "k"})
    assert r.status_code == 403


def test_collection_shape_count_and_select(api):
    c, _ = api
    r = c.get("/odata/Invoices", params={"$top": 3, "$select": "invoice_id,total"}, headers=READ).json()
    assert len(r["value"]) == 3 and set(r["value"][0]) == {"invoice_id", "total"} and r["@odata.count"] > 3 and "@odata.nextLink" in r


def test_bad_queries_are_400_not_500(api):
    c, _ = api
    for params in ({"$filter": "nope eq 1"}, {"$top": 99999}, {"$orderby": "x; y"}, {"$select": "secret"}):
        assert c.get("/odata/Invoices", params=params, headers=READ).status_code == 400


def test_single_entity_expand_and_404(api):
    c, _ = api
    po = c.get("/odata/PurchaseOrders/PO-100001", params={"$expand": "Items"}, headers=READ).json()
    assert po["po_number"] == "PO-100001" and po["Items"] and po["Items"][0]["line_no"] == 1
    assert c.get("/odata/PurchaseOrders/NOPE", headers=READ).status_code == 404
    assert c.get("/odata/Invoices/INV-000001", params={"$expand": "Items"}, headers=READ).status_code == 400


def test_pagination_complete_and_without_duplicates(api):
    cl, _ = client(api)
    total = api[0].get("/odata/Invoices", params={"$top": 1}, headers=READ).json()["@odata.count"]
    ids = [r["invoice_id"] for r in cl.iterate("Invoices", page_size=37)]
    assert len(ids) == len(set(ids)) == total


def test_filtered_iteration_matches_filter(api):
    cl, _ = client(api)
    rows = list(cl.iterate("Invoices", "total ge 5000 and status ne 'rejected'", page_size=25, orderby="total desc"))
    assert rows and all(r["total"] >= 5000 and r["status"] != "rejected" for r in rows)
    assert [r["total"] for r in rows] == sorted((r["total"] for r in rows), reverse=True)


# ---------------- retries
def test_retries_transient_then_succeeds_with_backoff(api):
    cl, sleeps = client(api)
    api[0].post("/_test/fail", params={"n": 2, "status": 503})
    assert cl.get_invoice("INV-000001")["invoice_id"] == "INV-000001"
    assert cl.attempts == 3 and len(sleeps) == 2 and sleeps[1] >= sleeps[0] * 0.5


def test_retry_after_is_honoured(api):
    cl, sleeps = client(api)
    api[0].post("/_test/fail", params={"n": 1, "status": 429, "retry_after": 3})
    cl.get_invoice("INV-000001")
    assert sleeps == [3.0]


def test_gives_up_after_max_retries(api):
    cl, sleeps = client(api, max_retries=2)
    api[0].post("/_test/fail", params={"n": 10, "status": 503})
    with pytest.raises(ErpUnavailable):
        cl.get_invoice("INV-000001")
    assert cl.attempts == 3


def test_client_errors_are_not_retried(api):
    cl, sleeps = client(api)
    with pytest.raises(ErpError) as e:
        cl.get_invoice("MISSING")
    assert e.value.status == 404 and cl.attempts == 1 and not sleeps


def test_transport_errors_retry_then_raise():
    calls = []

    def boom(request):
        calls.append(1)
        raise httpx.ConnectError("down")

    cl = ErpClient(httpx.Client(transport=httpx.MockTransport(boom), base_url="http://erp"), sleep=lambda s: None, max_retries=2)
    with pytest.raises(ErpUnavailable):
        cl.get_invoice("X")
    assert len(calls) == 3


# ---------------- writes: idempotency & concurrency
def test_payment_block_is_idempotent(api):
    cl, _ = client(api, "write-token")
    a = cl.place_payment_block("INV-000001", "suspected duplicate", "tester", idempotency_key="op-1")
    b = cl.place_payment_block("INV-000001", "suspected duplicate", "tester", idempotency_key="op-1")
    assert a["block_id"] == b["block_id"] and not a["replayed"] and b["replayed"]
    assert len(api[0].get("/odata/PaymentBlocks", params={"invoice_id": "INV-000001"}, headers=READ).json()["value"]) == 1


def test_idempotency_key_reuse_with_different_body_rejected(api):
    cl, _ = client(api, "write-token")
    cl.place_payment_block("INV-000001", "reason one", "t", idempotency_key="op-2")
    with pytest.raises(ErpError) as e:
        cl.place_payment_block("INV-000002", "reason one", "t", idempotency_key="op-2")
    assert e.value.status == 422


def test_retry_after_lost_response_does_not_double_block(api):
    """Server applied the write but the first response was a 503: the retry uses the same key and must not duplicate."""
    c, app = api
    cl, _ = client(api, "write-token")
    body = {"invoice_id": "INV-000003", "reason": "lost response", "requested_by": "t"}
    c.post("/odata/PaymentBlocks", json=body, headers={**WRITE, "Idempotency-Key": "op-3"})
    c.post("/_test/fail", params={"n": 1, "status": 503})
    out = cl.place_payment_block("INV-000003", "lost response", "t", idempotency_key="op-3")
    assert out["replayed"] and len(c.get("/odata/PaymentBlocks", params={"invoice_id": "INV-000003"}, headers=READ).json()["value"]) == 1


def test_payment_block_validation(api):
    c, _ = api
    h = {**WRITE, "Idempotency-Key": "v1"}
    assert c.post("/odata/PaymentBlocks", json={"invoice_id": "INV-000001", "reason": "x", "requested_by": "t"}, headers=h).status_code == 422
    assert c.post("/odata/PaymentBlocks", json={"invoice_id": "NOPE", "reason": "valid reason", "requested_by": "t"}, headers=h).status_code == 404
    assert c.post("/odata/PaymentBlocks", json={"invoice_id": "INV-000001", "reason": "valid reason", "requested_by": "t"}, headers=WRITE).status_code == 400


def test_optimistic_concurrency_and_state_machine(api):
    cl, _ = client(api, "write-token")
    inv = cl.get_invoice("INV-000010")
    start = inv["status"]
    c = api[0]
    assert c.patch("/odata/Invoices/INV-000010", json={"status": "rejected"}, headers=WRITE).status_code == 428     # If-Match required
    assert c.patch("/odata/Invoices/INV-000010", json={"status": "bogus"}, headers={**WRITE, "If-Match": inv["etag"]}).status_code == 422
    assert c.patch("/odata/Invoices/INV-000010", json={"status": "rejected"}, headers={**WRITE, "If-Match": 'W/"999"'}).status_code == 412
    if start == "paid":
        assert c.patch("/odata/Invoices/INV-000010", json={"status": "received"}, headers={**WRITE, "If-Match": inv["etag"]}).status_code == 409


def test_status_change_bumps_etag_and_blocks_stale_writer(api):
    c, app = api
    app.state.con.execute("UPDATE invoice SET status='extracted' WHERE invoice_id='INV-000020'")
    app.state.con.commit()
    cl, _ = client(api, "write-token")
    e1 = cl.get_invoice("INV-000020")["etag"]
    cl.set_invoice_status("INV-000020", "matched", e1)
    e2 = cl.get_invoice("INV-000020")["etag"]
    assert e2 != e1
    with pytest.raises(ErpError) as e:
        cl.set_invoice_status("INV-000020", "approved", e1)                # stale etag
    assert e.value.status == 412
    cl.set_invoice_status("INV-000020", "approved", e2)
    with pytest.raises(ErpError) as e:
        cl.set_invoice_status("INV-000020", "matched", cl.get_invoice("INV-000020")["etag"])   # approved -> matched illegal
    assert e.value.status == 409


def test_test_hooks_disabled_by_default(csv_dir, monkeypatch):
    monkeypatch.delenv("ERP_TEST_HOOKS", raising=False)
    c = TestClient(create_app(str(csv_dir)))
    assert c.post("/_test/fail").status_code == 404


def test_responses_match_declared_schema(api):
    c, _ = api
    inv = c.get("/odata/Invoices/INV-000001", headers=READ).json()
    assert set(inv) == {"invoice_id", "supplier_id", "invoice_number", "po_number", "invoice_date", "due_date", "currency", "subtotal", "tax", "total", "status"}
    json.dumps(inv)
