"""ERP client with the failure handling a real integration needs: retries with backoff, Retry-After, idempotency keys,
pagination, optimistic concurrency, typed results. ``ErpPort`` is the interface a real SAP/Oracle connector must implement.
"""
from __future__ import annotations

import random
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any, Protocol

import httpx

RETRY_STATUSES = {429, 502, 503, 504}


class ErpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"ERP {status}: {message}")
        self.status, self.message = status, message


class ErpUnavailable(ErpError):
    """Retries exhausted on a transient failure."""


class ErpPort(Protocol):
    def get_invoice(self, invoice_id: str) -> dict: ...
    def get_purchase_order(self, po_number: str) -> dict: ...
    def list_receipts(self, po_number: str) -> list[dict]: ...
    def place_payment_block(self, invoice_id: str, reason: str, requested_by: str, idempotency_key: str | None = None) -> dict: ...


class ErpClient:
    def __init__(self, http: httpx.Client, token: str = "read-token", max_retries: int = 4, base_delay: float = 0.2,
                 max_delay: float = 5.0, sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None):
        self.http, self.token = http, token
        self.max_retries, self.base_delay, self.max_delay = max_retries, base_delay, max_delay
        self._sleep, self._rng = sleep, rng or random.Random(0)
        self.attempts = 0

    # ------------------------------------------------------------- transport
    def _delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), self.max_delay)
        return min(self.max_delay, self.base_delay * 2 ** attempt) * (0.5 + self._rng.random() / 2)      # jittered backoff

    def _request(self, method: str, url: str, retry: bool = True, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token}", **kw.pop("headers", {})}
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.attempts += 1
            try:
                r = self.http.request(method, url, headers=headers, **kw)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last = exc
                if not retry or attempt == self.max_retries:
                    raise ErpUnavailable(0, f"transport error: {exc}") from exc
                self._sleep(self._delay(attempt, None))
                continue
            if r.status_code in RETRY_STATUSES and retry:
                if attempt == self.max_retries:
                    raise ErpUnavailable(r.status_code, "retries exhausted")
                self._sleep(self._delay(attempt, r.headers.get("Retry-After")))
                continue
            if r.status_code >= 400:
                msg = r.json().get("error", {}).get("message", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
                raise ErpError(r.status_code, msg)
            return r
        raise ErpUnavailable(0, f"unreachable: {last}")

    # ------------------------------------------------------------- reads
    def get(self, entity: str, key: str, expand: str | None = None) -> dict:
        return self._request("GET", f"/odata/{entity}/{key}", params={"$expand": expand} if expand else None).json()

    def iterate(self, entity: str, filter: str | None = None, page_size: int = 50, orderby: str | None = None,
                max_items: int = 100_000) -> Iterator[dict]:
        params: dict[str, Any] = {"$top": page_size}
        if filter:
            params["$filter"] = filter
        if orderby:
            params["$orderby"] = orderby
        url, seen = f"/odata/{entity}", 0
        while url:
            body = self._request("GET", url, params=params).json()
            for row in body["value"]:
                seen += 1
                if seen > max_items:
                    raise ErpError(0, "max_items exceeded; refine the filter")
                yield row
            url, params = body.get("@odata.nextLink"), None

    def get_invoice(self, invoice_id: str) -> dict:
        r = self._request("GET", f"/odata/Invoices/{invoice_id}")
        return {**r.json(), "etag": r.headers.get("ETag")}

    def get_purchase_order(self, po_number: str) -> dict:
        return self.get("PurchaseOrders", po_number, expand="Items")

    def list_receipts(self, po_number: str) -> list[dict]:
        return list(self.iterate("GoodsReceipts", f"po_number eq '{po_number.replace(chr(39), chr(39) * 2)}'"))

    # ------------------------------------------------------------- writes
    def place_payment_block(self, invoice_id: str, reason: str, requested_by: str, idempotency_key: str | None = None) -> dict:
        key = idempotency_key or str(uuid.uuid4())             # reuse the SAME key when retrying a logical operation
        r = self._request("POST", "/odata/PaymentBlocks", json={"invoice_id": invoice_id, "reason": reason, "requested_by": requested_by},
                          headers={"Idempotency-Key": key})
        return {**r.json(), "replayed": r.headers.get("Idempotent-Replay") == "true"}

    def set_invoice_status(self, invoice_id: str, status: str, etag: str) -> dict:
        return self._request("PATCH", f"/odata/Invoices/{invoice_id}", json={"status": status}, headers={"If-Match": etag},
                             retry=False).json()                   # non-idempotent without a key: never auto-retry
