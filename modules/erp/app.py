"""Mock ERP: OData-style read API plus two guarded writes (invoice status change, payment block).

It exists so Workflows 5-7 can be built and tested against a stable contract. A real SAP/Oracle connector must satisfy
the same ``ErpPort`` interface (see client.py); nothing above that interface may depend on mock behaviour.

Test hooks (``/_test/*``) inject failures so retry logic can be tested; they are disabled unless ERP_TEST_HOOKS=1.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import odata
from .store import CHILDREN, ENTITIES, connect, seed

TOKENS = {"read-token": {"read"}, "write-token": {"read", "write"}}
INVOICE_STATUSES = {"received", "extracted", "matched", "exception", "approved", "paid", "rejected"}
ALLOWED_TRANSITIONS = {"received": {"extracted", "rejected"}, "extracted": {"matched", "exception", "rejected"},
                       "matched": {"approved", "rejected"}, "exception": {"approved", "rejected", "matched"},
                       "approved": {"paid"}, "paid": set(), "rejected": set()}


class StatusChange(BaseModel):
    status: str

class Block(BaseModel):
    invoice_id: str
    reason: str = Field(min_length=3, max_length=500)
    requested_by: str = Field(min_length=1, max_length=100)


def create_app(csv_dir: str, db_path: str = ":memory:") -> FastAPI:
    con: sqlite3.Connection = connect(db_path)
    seed(con, csv_dir)
    app = FastAPI(title="OpsPilot Mock ERP", version="0.1.0")
    app.state.con = con
    app.state.fail = {"n": 0, "status": 503, "retry_after": None}
    app.state.calls = 0

    @app.middleware("http")
    async def chaos(request: Request, call_next):
        app.state.calls += 1
        if os.getenv("ERP_TEST_HOOKS") == "1" and not request.url.path.startswith("/_test") and app.state.fail["n"] > 0:
            app.state.fail["n"] -= 1
            headers = {"Retry-After": str(app.state.fail["retry_after"])} if app.state.fail["retry_after"] is not None else {}
            return JSONResponse({"error": {"code": "injected", "message": "injected failure"}}, status_code=app.state.fail["status"], headers=headers)
        return await call_next(request)

    def auth(authorization: str | None, scope: str) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, detail="missing bearer token")
        scopes = TOKENS.get(authorization[7:])
        if scopes is None:
            raise HTTPException(401, detail="invalid token")
        if scope not in scopes:
            raise HTTPException(403, detail=f"token lacks '{scope}' scope")

    @app.exception_handler(HTTPException)
    async def http_err(_: Request, exc: HTTPException):
        return JSONResponse({"error": {"code": str(exc.status_code), "message": exc.detail}}, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(odata.ODataError)
    async def odata_err(_: Request, exc: odata.ODataError):
        return JSONResponse({"error": {"code": "400", "message": str(exc)}}, status_code=400)

    def _rows(entity: str, q: odata.Query, extra_where: str = "", extra_params: list | None = None) -> list[dict]:
        table, key, cols = ENTITIES[entity]
        sel = ", ".join(q.select or cols)
        where = q.where + (f" AND {extra_where}" if extra_where else "")
        sql = f"SELECT {sel} FROM {table} WHERE {where} ORDER BY {q.order} LIMIT ? OFFSET ?"
        return [dict(r) for r in con.execute(sql, [*q.params, *(extra_params or []), q.top, q.skip])]

    def collection(entity: str, request: Request, filter, orderby, top, skip, select, authorization):
        auth(authorization, "read")
        table, key, cols = ENTITIES[entity]
        q = odata.build(set(cols), filter, orderby, top, skip, select, f"{key} ASC")
        rows = _rows(entity, q)
        total = con.execute(f"SELECT count(*) FROM {table} WHERE {q.where}", q.params).fetchone()[0]
        body: dict[str, Any] = {"value": rows, "@odata.count": total}
        if q.skip + len(rows) < total and q.top > 0:
            params = dict(request.query_params)
            params["$skip"] = str(q.skip + q.top)
            params["$top"] = str(q.top)
            body["@odata.nextLink"] = f"{request.url.path}?" + "&".join(f"{k}={v}" for k, v in params.items())
        return body

    def single(entity: str, key_value: str, expand: str | None, authorization):
        auth(authorization, "read")
        table, key, cols = ENTITIES[entity]
        extra = ", version" if entity == "Invoices" else ""
        row = con.execute(f"SELECT {', '.join(cols)}{extra} FROM {table} WHERE {key} = ?", [key_value]).fetchone()
        if row is None:
            raise HTTPException(404, detail=f"{entity}('{key_value}') not found")
        d = dict(row)
        resp_headers = {}
        if entity == "Invoices":
            resp_headers["ETag"] = f'W/"{d.pop("version")}"'
        if expand:
            if expand != "Items" or entity not in CHILDREN:
                raise HTTPException(400, detail=f"$expand={expand} not supported for {entity}")
            ct, ck, ccols = CHILDREN[entity]
            d["Items"] = [dict(r) for r in con.execute(f"SELECT {', '.join(ccols)} FROM {ct} WHERE {ck} = ? ORDER BY line_no", [key_value])]
        return JSONResponse(d, headers=resp_headers)

    for entity in ENTITIES:
        def make(entity=entity):
            @app.get(f"/odata/{entity}", name=f"list_{entity}")
            def _list(request: Request, filter: str | None = Query(None, alias="$filter"), orderby: str | None = Query(None, alias="$orderby"),
                      top: int | None = Query(None, alias="$top"), skip: int | None = Query(None, alias="$skip"),
                      select: str | None = Query(None, alias="$select"), authorization: str | None = Header(None)):
                return collection(entity, request, filter, orderby, top, skip, select, authorization)

            @app.get(f"/odata/{entity}/{{key}}", name=f"get_{entity}")
            def _get(key: str, expand: str | None = Query(None, alias="$expand"), authorization: str | None = Header(None)):
                return single(entity, key, expand, authorization)
        make()

    @app.patch("/odata/Invoices/{invoice_id}")
    def patch_invoice(invoice_id: str, body: StatusChange, if_match: str | None = Header(None), authorization: str | None = Header(None)):
        auth(authorization, "write")
        if body.status not in INVOICE_STATUSES:
            raise HTTPException(422, detail=f"status must be one of {sorted(INVOICE_STATUSES)}")
        if if_match is None:
            raise HTTPException(428, detail="If-Match header required")
        row = con.execute("SELECT status, version FROM invoice WHERE invoice_id = ?", [invoice_id]).fetchone()
        if row is None:
            raise HTTPException(404, detail="invoice not found")
        if if_match != f'W/"{row["version"]}"':
            raise HTTPException(412, detail="invoice was modified by someone else; re-read and retry")
        if body.status not in ALLOWED_TRANSITIONS[row["status"]]:
            raise HTTPException(409, detail=f"transition {row['status']} -> {body.status} is not allowed")
        cur = con.execute("UPDATE invoice SET status = ?, version = version + 1 WHERE invoice_id = ? AND version = ?",
                          [body.status, invoice_id, row["version"]])
        con.commit()
        if cur.rowcount != 1:
            raise HTTPException(412, detail="concurrent modification")
        return JSONResponse({"invoice_id": invoice_id, "status": body.status}, headers={"ETag": f'W/"{row["version"] + 1}"'})

    @app.post("/odata/PaymentBlocks", status_code=201)
    def create_block(body: Block, response: Response, idempotency_key: str | None = Header(None), authorization: str | None = Header(None)):
        auth(authorization, "write")
        if not idempotency_key or len(idempotency_key) > 100:
            raise HTTPException(400, detail="Idempotency-Key header required (<= 100 chars)")
        req_hash = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
        prev = con.execute("SELECT request_hash, status, body FROM idempotency WHERE key = ?", [idempotency_key]).fetchone()
        if prev:
            if prev["request_hash"] != req_hash:
                raise HTTPException(422, detail="Idempotency-Key reused with a different request body")
            response.headers["Idempotent-Replay"] = "true"
            response.status_code = prev["status"]
            return json.loads(prev["body"])
        if con.execute("SELECT 1 FROM invoice WHERE invoice_id = ?", [body.invoice_id]).fetchone() is None:
            raise HTTPException(404, detail="invoice not found")
        con.execute("BEGIN IMMEDIATE") if not con.in_transaction else None
        cur = con.execute("INSERT INTO payment_block (invoice_id, reason, created_by) VALUES (?,?,?)",
                          [body.invoice_id, body.reason, body.requested_by])
        out = {"block_id": cur.lastrowid, "invoice_id": body.invoice_id, "reason": body.reason}
        con.execute("INSERT INTO idempotency (key, request_hash, status, body) VALUES (?,?,?,?)",
                    [idempotency_key, req_hash, 201, json.dumps(out)])
        con.commit()
        return out

    @app.get("/odata/PaymentBlocks")
    def list_blocks(invoice_id: str | None = None, authorization: str | None = Header(None)):
        auth(authorization, "read")
        sql, p = "SELECT block_id, invoice_id, reason, created_by FROM payment_block", []
        if invoice_id:
            sql, p = sql + " WHERE invoice_id = ?", [invoice_id]
        return {"value": [dict(r) for r in con.execute(sql + " ORDER BY block_id", p)]}

    @app.post("/_test/fail")
    def set_fail(n: int = 1, status: int = 503, retry_after: int | None = None):
        if os.getenv("ERP_TEST_HOOKS") != "1":
            raise HTTPException(404, detail="not found")
        app.state.fail.update(n=n, status=status, retry_after=retry_after)
        app.state.calls = 0
        return app.state.fail

    return app


def app_from_env() -> FastAPI:                                 # uvicorn modules.erp.app:app_from_env --factory
    return create_app(os.getenv("ERP_CSV_DIR", "datasets/synthetic/procurement"), os.getenv("ERP_DB", ":memory:"))
