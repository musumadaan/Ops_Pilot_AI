"""Seeded synthetic procurement history with injected, labelled defects.

Everything is invented. The generator first builds a *clean* history (POs -> receipts ->
invoices -> payments) that triggers no exceptions, then injects defects of each type and
records them in ``labels/anomaly_labels.csv`` so the SQL rules can be tested against known answers.

    python scripts/generate_master_data.py --out datasets/synthetic/procurement --seed 42

Determinism: one ``random.Random(seed)``; all iteration is over sorted lists.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

CENT = Decimal("0.01")
TAX_RATE = Decimal("0.05")                  # GST (synthetic)

CATEGORY_ITEMS: dict[str, list[tuple[str, str, int, int]]] = {   # description, uom, min, max price
    "Raw materials": [("Steel plate 6mm", "SHEET", 120, 260), ("Aluminum bar stock", "M", 18, 45),
                      ("Copper wire spool", "EA", 90, 240), ("Welding rod 5kg", "BOX", 35, 80)],
    "Fasteners": [("Hex bolt M10 box", "BOX", 25, 60), ("Anchor set", "BOX", 40, 110),
                  ("Lock washer carton", "CTN", 15, 35), ("Threaded rod 1m", "EA", 6, 18)],
    "Logistics": [("Freight Calgary-Edmonton", "TRIP", 450, 900), ("Pallet storage per week", "WK", 35, 70),
                  ("Courier express parcel", "EA", 18, 55), ("Cross-dock handling", "HR", 60, 120)],
    "Office supplies": [("Copy paper case", "CTN", 38, 62), ("Toner cartridge", "EA", 90, 210),
                        ("Desk chair ergonomic", "EA", 180, 420), ("Whiteboard 4x6", "EA", 70, 160)],
    "Maintenance": [("Hydraulic hose assembly", "EA", 55, 190), ("Safety harness", "EA", 85, 240),
                    ("Industrial cleaner 20L", "EA", 40, 95), ("Electrical contactor", "EA", 60, 210)],
    "IT hardware": [("Laptop 14in business", "EA", 1100, 1900), ("Network switch 24p", "EA", 350, 900),
                    ("Cat6 cable 305m", "EA", 120, 260), ("Software licence annual", "EA", 150, 700)],
    "Packaging": [("Pallet 48x40", "EA", 14, 28), ("Stretch wrap roll", "EA", 12, 30),
                  ("Corrugated crate", "EA", 22, 75), ("Shipping labels roll", "EA", 9, 24)],
}
KEYWORDS = [
    (("steel", "welding", "granite", "chemical", "building", "agricultural"), "Raw materials"),
    (("fastener",), "Fasteners"), (("logistics", "courier", "catering"), "Logistics"),
    (("office", "printing", "furniture"), "Office supplies"),
    (("it hardware", "software", "cabling", "lab"), "IT hardware"),
    (("packaging", "pallet"), "Packaging"),
]
INSTITUTIONS = ["Prairie Credit Union", "Northern Bank", "Western Trust", "Foothills Savings"]
ANOMALY_TYPES = [
    "PRICE_VARIANCE", "QTY_OVER_RECEIPT", "NO_RECEIPT", "NO_PO", "DUPLICATE_INVOICE_NUMBER",
    "POSSIBLE_DUPLICATE", "DATE_BEFORE_PO", "BANK_ACCOUNT_CHANGED", "SPLIT_BELOW_THRESHOLD",
    "WEEKEND_POSTING", "ROUND_AMOUNT",
]


def d2(x) -> Decimal:
    return Decimal(x).quantize(CENT, ROUND_HALF_UP)


@dataclass
class POLine:
    line_no: int
    description: str
    uom: str
    qty: Decimal
    unit_price: Decimal


@dataclass
class Receipt:
    receipt_id: str
    po_number: str
    received_date: date
    received_by: str
    qty: dict[int, Decimal]


@dataclass
class PO:
    po_number: str
    supplier_id: str
    buyer_id: str
    category: str
    order_date: date
    cost_center: str
    lines: list[POLine]
    receipts: list[Receipt] = field(default_factory=list)


@dataclass
class InvLine:
    line_no: int
    po_number: str | None
    po_line_no: int | None
    description: str
    qty: Decimal
    unit_price: Decimal
    amount: Decimal = Decimal(0)


@dataclass
class Invoice:
    supplier_id: str
    invoice_number: str
    po_number: str | None
    invoice_date: date
    received_at: datetime
    lines: list[InvLine]
    terms_days: int
    approved_at: datetime | None = None
    bank_account_id: str | None = None
    paid_date: date | None = None
    invoice_id: str = ""
    subtotal: Decimal = Decimal(0)
    tax: Decimal = Decimal(0)
    total: Decimal = Decimal(0)

    @property
    def due_date(self) -> date:
        return self.invoice_date + timedelta(days=self.terms_days)

    def recompute(self) -> None:
        for ln in self.lines:
            ln.amount = d2(ln.qty * ln.unit_price)
        self.subtotal = sum((ln.amount for ln in self.lines), Decimal(0))
        self.tax = d2(self.subtotal * TAX_RATE)
        self.total = self.subtotal + self.tax
        if self.total >= 1000 and self.total % 1000 == 0:     # keep clean data free of round totals
            self.tax += CENT
            self.total += CENT


def at_utc(d: date, rng: random.Random) -> datetime:
    return datetime(d.year, d.month, d.day, 16, rng.randint(0, 59), tzinfo=UTC)


def next_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def supplier_category(name: str) -> str:
    low = name.lower()
    for words, cat in KEYWORDS:
        if any(w in low for w in words):
            return cat
    return "Maintenance"


class Generator:
    def __init__(self, suppliers_csv: Path, seed: int = 42, n_po: int = 400, per_type: int = 4,
                 start: date = date(2025, 10, 1), as_of: date = date(2026, 10, 15), threshold: int = 10000):
        self.rng = random.Random(seed)
        self.seed, self.n_po, self.per_type = seed, n_po, per_type
        self.start, self.as_of, self.threshold = start, as_of, Decimal(threshold)
        with open(suppliers_csv, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.suppliers = [
            {"supplier_id": r["supplier_id"], "name": r["name"], "country": "CA",
             "payment_terms_days": self.rng.choice([15, 30, 30, 45, 60]),
             "status": "active", "created_on": date(2022, 1, 1) + timedelta(days=self.rng.randint(0, 900)),
             "category": supplier_category(r["name"])}
            for r in sorted(rows, key=lambda r: r["supplier_id"])
        ]
        self.suppliers[-1]["status"] = "on_hold"
        self.weights = [1 / (i + 1) ** 0.8 for i in range(len(self.suppliers))]   # concentration
        self.pos: list[PO] = []
        self.invoices: list[Invoice] = []
        self.labels: list[dict] = []
        self.used_numbers: set[tuple[str, str]] = set()
        self.po_seq = 100000
        self.rcpt_seq = 0
        self.employees = self._employees()
        self.buyers = [e["employee_id"] for e in self.employees if e["role"] == "buyer"]
        self.receivers = [e["employee_id"] for e in self.employees if e["role"] in ("buyer", "ap_analyst")]

    # ------------------------------------------------------------------ master data
    @staticmethod
    def _employees() -> list[dict]:
        def e(i, name, role, mgr, lim):
            return {"employee_id": f"E{i:03d}", "name": name, "role": role, "manager_id": mgr, "approval_limit": lim}
        rows = [e(1, "Avery Chen", "executive", "", 1_000_000),
                e(2, "Morgan Singh", "director", "E001", 100_000), e(3, "Riley Tremblay", "director", "E001", 100_000)]
        for i in range(4, 8):
            rows.append(e(i, f"Manager {i}", "manager", "E002" if i < 6 else "E003", 25_000))
        for i in range(8, 18):
            rows.append(e(i, f"Buyer {i}", "buyer", f"E{4 + (i - 8) % 4:03d}", 5_000))
        for i in range(18, 21):
            rows.append(e(i, f"AP Analyst {i}", "ap_analyst", "E004", 0))
        return rows

    # ------------------------------------------------------------------ clean history
    def _new_number(self, supplier_id: str) -> str:
        prefix = "".join(c for c in supplier_id if c.isdigit())[-3:]
        while True:
            num = f"S{prefix}-{self.rng.randint(100000, 999999)}"
            if (supplier_id, num) not in self.used_numbers:
                self.used_numbers.add((supplier_id, num))
                return num

    def _make_po(self, supplier: dict, order_date: date, lines: list[POLine] | None = None) -> PO:
        self.po_seq += 1
        cat = supplier["category"]
        if lines is None:
            items = self.rng.sample(CATEGORY_ITEMS[cat], k=self.rng.randint(1, min(4, len(CATEGORY_ITEMS[cat]))))
            lines = []
            for n, (desc, uom, lo, hi) in enumerate(items, 1):
                price = d2(self.rng.uniform(lo, hi))
                qty = Decimal(self.rng.randint(1, 8 if price > 500 else 120))
                lines.append(POLine(n, desc, uom, qty, price))
        po = PO(f"PO-{self.po_seq}", supplier["supplier_id"], self.rng.choice(self.buyers), cat, order_date,
                f"CC{sorted(CATEGORY_ITEMS).index(cat) + 101}", lines)
        self.pos.append(po)
        return po

    def _receive(self, po: PO, parts: int) -> None:
        """Split each line's ordered qty into ``parts`` receipts (1 or 2)."""
        first = po.order_date + timedelta(days=self.rng.randint(3, 30))
        dates = [first] + ([first + timedelta(days=self.rng.randint(4, 14))] if parts == 2 else [])
        shares = [Decimal(1)] if parts == 1 else [Decimal("0.6"), Decimal("0.4")]
        for k, (rd, share) in enumerate(zip(dates, shares)):
            self.rcpt_seq += 1
            qty = {}
            for ln in po.lines:
                q = ln.qty * share if parts == 1 else (
                    (ln.qty * Decimal("0.6")).to_integral_value(ROUND_HALF_UP) if k == 0
                    else ln.qty - (ln.qty * Decimal("0.6")).to_integral_value(ROUND_HALF_UP))
                qty[ln.line_no] = q
            po.receipts.append(Receipt(f"GR-{self.rcpt_seq:06d}", po.po_number, rd,
                                       self.rng.choice(self.receivers), qty))

    def _invoice_for(self, po: PO, receipts: list[Receipt], sup: dict, qty_by_line: dict[int, Decimal],
                     invoice_date: date) -> Invoice:
        recv_day = next_weekday(invoice_date + timedelta(days=self.rng.randint(0, 3)))
        lines = [InvLine(n, po.po_number, ln.line_no, ln.description, qty_by_line[ln.line_no], ln.unit_price)
                 for n, ln in enumerate(po.lines, 1) if qty_by_line[ln.line_no] > 0]
        inv = Invoice(po.supplier_id, self._new_number(po.supplier_id), po.po_number, invoice_date,
                      at_utc(recv_day, self.rng), lines, sup["payment_terms_days"])
        inv.recompute()
        return inv

    def build_clean(self) -> None:
        sup_by_id = {s["supplier_id"]: s for s in self.suppliers}
        latest_po = self.as_of - timedelta(days=50)
        span = (latest_po - self.start).days
        for _ in range(self.n_po):
            sup = self.rng.choices(self.suppliers, weights=self.weights, k=1)[0]
            po = self._make_po(sup, self.start + timedelta(days=self.rng.randint(0, span)))
            parts = 2 if self.rng.random() < 0.2 else 1
            self._receive(po, parts)
            recent = po.order_date > self.as_of - timedelta(days=90)
            if recent and self.rng.random() < 0.5:
                continue                                         # still open: received, not yet invoiced
            if parts == 2 and self.rng.random() < 0.5:           # two partial invoices
                for r in po.receipts:
                    self.invoices.append(self._invoice_for(
                        po, [r], sup, r.qty, r.received_date + timedelta(days=self.rng.randint(2, 12))))
            else:
                total_qty = {ln.line_no: sum(r.qty[ln.line_no] for r in po.receipts) for ln in po.lines}
                self.invoices.append(self._invoice_for(
                    po, po.receipts, sup, total_qty,
                    po.receipts[-1].received_date + timedelta(days=self.rng.randint(2, 20))))
        for inv in self.invoices:
            self._schedule(inv, sup_by_id[inv.supplier_id])
        self.invoices = [i for i in self.invoices if i.invoice_date <= self.as_of]

    def _schedule(self, inv: Invoice, sup: dict) -> None:
        """Approval and payment timeline (cycle time is log-normal: median ~4 days, long tail)."""
        delay = min(45.0, max(0.5, self.rng.lognormvariate(math.log(4), 0.6)))
        approved = (inv.received_at + timedelta(days=delay)).replace(microsecond=0)
        inv.approved_at = approved if approved.date() <= self.as_of else None
        inv.paid_date = None
        if inv.approved_at:
            paid = max(inv.approved_at.date() + timedelta(days=1), inv.due_date + timedelta(days=self.rng.randint(-9, 6)))
            if paid <= self.as_of:
                inv.paid_date = paid

    # ------------------------------------------------------------------ defect injection
    def _single_invoice_pos(self) -> list[Invoice]:
        count: dict[str, int] = {}
        for i in self.invoices:
            count[i.po_number or ""] = count.get(i.po_number or "", 0) + 1
        return [i for i in self.invoices if i.po_number and count[i.po_number] == 1]

    def _label(self, inv: Invoice, kind: str, expected: str | None, note: str = "") -> None:
        self.labels.append({"anomaly_type": kind, "expected_code": expected or "", "note": note,
                            "_inv": inv})

    def inject(self) -> None:
        sup_by_id = {s["supplier_id"]: s for s in self.suppliers}
        po_by = {p.po_number: p for p in self.pos}
        pool = sorted(self._single_invoice_pos(), key=lambda i: (i.invoice_date, i.invoice_number))
        pool = [i for i in pool if i.invoice_date <= self.as_of - timedelta(days=20)]
        k = self.per_type

        def take(n: int) -> list[Invoice]:
            picks = self.rng.sample(pool, n)
            for p in picks:
                pool.remove(p)
            return picks

        for inv in take(k):                                                    # price variance
            ln = self.rng.choice(inv.lines)
            ln.unit_price = d2(ln.unit_price * Decimal(str(round(self.rng.uniform(1.07, 1.25), 3))))
            inv.recompute()
            self._label(inv, "PRICE_VARIANCE", "PRICE_VARIANCE", f"line {ln.line_no} price raised")
        for inv in take(k):                                                    # qty over receipt
            ln = self.rng.choice(inv.lines)
            ln.qty = (ln.qty * Decimal(str(round(self.rng.uniform(1.15, 1.4), 2)))).to_integral_value(ROUND_HALF_UP) + 1
            inv.recompute()
            self._label(inv, "QTY_OVER_RECEIPT", "QTY_OVER_RECEIPT", f"line {ln.line_no} qty inflated")
        for inv in take(k):                                                    # no receipt
            po = po_by[inv.po_number]
            po.receipts = []
            self._label(inv, "NO_RECEIPT", "NO_RECEIPT", "receipts removed")
        for inv in take(k):                                                    # no PO
            inv.po_number = None
            for ln in inv.lines:
                ln.po_number, ln.po_line_no = None, None
            self._label(inv, "NO_PO", "NO_PO", "PO reference removed")
        for inv in take(k):                                                    # date before PO
            po = po_by[inv.po_number]
            inv.invoice_date = po.order_date - timedelta(days=self.rng.randint(1, 10))
            inv.received_at = at_utc(next_weekday(inv.invoice_date), self.rng)
            self._schedule(inv, sup_by_id[inv.supplier_id])
            self._label(inv, "DATE_BEFORE_PO", "DATE_BEFORE_PO", "invoice dated before PO")
        for inv in take(k):                                                    # weekend posting
            d = inv.invoice_date
            while d.weekday() != 5:
                d += timedelta(days=1)
            inv.received_at = at_utc(d, self.rng)
            self._schedule(inv, sup_by_id[inv.supplier_id])
            self._label(inv, "WEEKEND_POSTING", "WEEKEND_POSTING", "received on a Saturday")
        for inv in take(k):                                                    # exact duplicate number
            dup = self._clone(inv, renumber=False)
            self._label(dup, "DUPLICATE_INVOICE_NUMBER", "DUPLICATE_INVOICE_NUMBER", f"clone of {inv.invoice_number}")
        for inv in take(k):                                                    # same total, new number
            dup = self._clone(inv, renumber=True)
            self._label(dup, "POSSIBLE_DUPLICATE", "POSSIBLE_DUPLICATE", f"re-keyed copy of {inv.invoice_number}")
        self._inject_new_pos(sup_by_id)

    def _clone(self, inv: Invoice, renumber: bool) -> Invoice:
        sup_terms = inv.terms_days
        number = self._new_number(inv.supplier_id) if renumber else self._variant(inv.invoice_number, inv.supplier_id)
        gap = self.rng.randint(3, 10)
        dup = Invoice(inv.supplier_id, number, inv.po_number, inv.invoice_date + timedelta(days=gap),
                      at_utc(next_weekday(inv.invoice_date + timedelta(days=gap)), self.rng),
                      [InvLine(l.line_no, l.po_number, l.po_line_no, l.description, l.qty, l.unit_price)
                       for l in inv.lines], sup_terms)
        dup.recompute()
        dup.subtotal, dup.tax, dup.total = inv.subtotal, inv.tax, inv.total      # identical totals
        self._schedule(dup, {"payment_terms_days": sup_terms})
        if dup.invoice_date <= self.as_of:
            self.invoices.append(dup)
        return dup

    def _variant(self, number: str, supplier_id: str) -> str:
        variant = number.replace("-", "") if self.rng.random() < 0.5 else number.lower()
        self.used_numbers.add((supplier_id, variant))
        return variant

    def _inject_new_pos(self, sup_by_id: dict[str, dict]) -> None:
        """Split-below-threshold groups and round-amount invoices need purpose-built POs."""
        for _ in range(self.per_type):                                          # round amounts
            sup = self.rng.choice(self.suppliers[:-1])
            d = self.start + timedelta(days=self.rng.randint(30, 240))
            po = self._make_po(sup, d, [POLine(1, f"{sup['category']} bulk order", "EA", Decimal(100), Decimal("200.00"))])
            self._receive(po, 1)
            inv = self._invoice_for(po, po.receipts, sup, {1: Decimal(100)}, po.receipts[-1].received_date + timedelta(days=5))
            inv.recompute()                                                     # 20000 + 1000 = 21000.00
            inv.tax, inv.total = d2(inv.subtotal * TAX_RATE), inv.subtotal + d2(inv.subtotal * TAX_RATE)
            self.invoices.append(inv)
            self._schedule(inv, sup)
            self._label(inv, "ROUND_AMOUNT", "ROUND_AMOUNT", "total is an exact multiple of 1000")
        n_groups = max(1, self.per_type // 2)
        for _ in range(n_groups):                                               # split invoices
            sup = self.rng.choice(self.suppliers[:-1])
            base = self.start + timedelta(days=self.rng.randint(30, 240))
            pieces = []
            for j in range(3):
                target = Decimal(str(round(self.rng.uniform(0.83, 0.96) * float(self.threshold), 2)))
                unit = d2(target / Decimal("1.05") / 10)
                po = self._make_po(sup, base + timedelta(days=j), [POLine(1, f"{sup['category']} lot {j + 1}", "EA", Decimal(10), unit)])
                self._receive(po, 1)
                inv = self._invoice_for(po, po.receipts, sup, {1: Decimal(10)}, base + timedelta(days=2 + j))
                po.receipts[0].received_date = base + timedelta(days=j + 1)
                self.invoices.append(inv)
                self._schedule(inv, sup)
                pieces.append(inv)
            for j, inv in enumerate(pieces):
                self._label(inv, "SPLIT_BELOW_THRESHOLD", "SPLIT_BELOW_THRESHOLD" if j else None,
                            "later piece of a split group" if j else "first piece (not detectable alone)")

    # ------------------------------------------------------------------ bank accounts (last)
    def finalize(self) -> None:
        sup_by_id = {s["supplier_id"]: s for s in self.suppliers}
        self.invoices = [i for i in self.invoices if i.invoice_date <= self.as_of]
        self.invoices.sort(key=lambda i: (i.invoice_date, i.received_at, i.invoice_number))
        for n, inv in enumerate(self.invoices, 1):
            inv.invoice_id = f"INV-{n:06d}"
        # accounts: one per supplier, plus a changed account for `per_type` suppliers
        self.accounts: list[dict] = []
        by_sup: dict[str, list[Invoice]] = {}
        for inv in self.invoices:
            by_sup.setdefault(inv.supplier_id, []).append(inv)
        eligible = [s for s in sorted(by_sup) if len(by_sup[s]) >= 6 and sup_by_id[s]["status"] == "active"]
        changing = set(self.rng.sample(eligible, min(self.per_type, len(eligible))))
        for sup in self.suppliers:
            sid = sup["supplier_id"]
            a = {"bank_account_id": f"BA-{sid[3:]}-A", "supplier_id": sid, "institution": self.rng.choice(INSTITUTIONS),
                 "account_last4": f"{self.rng.randint(0, 9999):04d}", "valid_from": sup["created_on"], "valid_to": ""}
            self.accounts.append(a)
            invs = by_sup.get(sid, [])
            if sid in changing:
                idx = self.rng.randint(3, len(invs) - 2)
                change_day = invs[idx].invoice_date
                b = {"bank_account_id": f"BA-{sid[3:]}-B", "supplier_id": sid, "institution": self.rng.choice(INSTITUTIONS),
                     "account_last4": f"{self.rng.randint(0, 9999):04d}", "valid_from": change_day, "valid_to": ""}
                a["valid_to"] = change_day
                self.accounts.append(b)
                for j, inv in enumerate(invs):
                    inv.bank_account_id = b["bank_account_id"] if j >= idx else a["bank_account_id"]
                self._label(invs[idx], "BANK_ACCOUNT_CHANGED", "BANK_ACCOUNT_CHANGED", "supplier switched account")
            else:
                for inv in invs:
                    inv.bank_account_id = a["bank_account_id"]
        # PO status: closed when fully invoiced, else open
        invoiced: dict[tuple[str, int], Decimal] = {}
        for inv in self.invoices:
            for ln in inv.lines:
                if ln.po_number:
                    invoiced[(ln.po_number, ln.po_line_no)] = invoiced.get((ln.po_number, ln.po_line_no), Decimal(0)) + ln.qty
        self.po_status = {p.po_number: ("closed" if all(invoiced.get((p.po_number, l.line_no), 0) >= l.qty for l in p.lines) else "open")
                          for p in self.pos}

    # ------------------------------------------------------------------ output
    def tables(self) -> dict[str, list[dict]]:
        f = lambda dt: dt.isoformat(sep=" ") if dt else ""
        t: dict[str, list[dict]] = {}
        t["supplier"] = [{k: s[k] for k in ("supplier_id", "name", "country", "payment_terms_days", "status", "created_on")}
                         for s in self.suppliers]
        t["supplier_bank_account"] = self.accounts
        t["employee"] = self.employees
        t["contract"] = [{"contract_id": f"CT-{s['supplier_id'][3:]}", "supplier_id": s["supplier_id"], "category": s["category"],
                          "effective_from": "2025-01-01", "effective_to": "",
                          "price_tolerance_pct": "5.00" if s["category"] == "Logistics" else "2.00"} for s in self.suppliers]
        t["purchase_order"] = [{"po_number": p.po_number, "supplier_id": p.supplier_id, "buyer_id": p.buyer_id, "category": p.category,
                                "currency": "CAD", "order_date": p.order_date, "status": self.po_status[p.po_number],
                                "cost_center": p.cost_center} for p in sorted(self.pos, key=lambda p: p.po_number)]
        t["po_line"] = [{"po_number": p.po_number, "line_no": l.line_no, "description": l.description, "uom": l.uom,
                         "qty_ordered": l.qty, "unit_price": l.unit_price} for p in sorted(self.pos, key=lambda p: p.po_number) for l in p.lines]
        rec = [r for p in sorted(self.pos, key=lambda p: p.po_number) for r in p.receipts]
        t["goods_receipt"] = [{"receipt_id": r.receipt_id, "po_number": r.po_number, "received_date": r.received_date,
                               "received_by": r.received_by} for r in sorted(rec, key=lambda r: r.receipt_id)]
        t["receipt_line"] = [{"receipt_id": r.receipt_id, "line_no": n, "po_number": r.po_number, "po_line_no": ln, "qty_received": q}
                             for r in sorted(rec, key=lambda r: r.receipt_id) for n, (ln, q) in enumerate(sorted(r.qty.items()), 1) if q > 0]
        invs = sorted(self.invoices, key=lambda i: i.invoice_id)
        t["invoice"] = [{"invoice_id": i.invoice_id, "supplier_id": i.supplier_id, "invoice_number": i.invoice_number,
                         "po_number": i.po_number or "", "invoice_date": i.invoice_date, "received_at": f(i.received_at),
                         "approved_at": f(i.approved_at), "due_date": i.due_date, "currency": "CAD", "subtotal": i.subtotal,
                         "tax": i.tax, "total": i.total, "bank_account_id": i.bank_account_id or "",
                         "status": "paid" if i.paid_date else "approved" if i.approved_at else "received",
                         "source": "edi", "source_sha256": ""} for i in invs]
        t["invoice_line"] = [{"invoice_id": i.invoice_id, "line_no": l.line_no, "po_number": l.po_number or "",
                              "po_line_no": l.po_line_no or "", "description": l.description, "qty": l.qty,
                              "unit_price": l.unit_price, "amount": l.amount} for i in invs for l in i.lines]
        t["payment"] = [{"payment_id": f"PAY-{n:06d}", "invoice_id": i.invoice_id, "paid_date": i.paid_date, "amount": i.total,
                         "bank_account_id": i.bank_account_id or "", "method": self.rng.choice(["eft", "eft", "eft", "wire", "cheque"])}
                        for n, i in enumerate([i for i in invs if i.paid_date], 1)]
        spend: dict[tuple[str, date], Decimal] = {}
        po_cat = {p.po_number: p.category for p in self.pos}
        for i in invs:
            key = (po_cat.get(i.po_number or "", "Uncategorized"), i.invoice_date.replace(day=1))
            spend[key] = spend.get(key, Decimal(0)) + i.subtotal
        t["budget"] = [{"category": c, "period": m, "amount": d2(v * Decimal(str(round(self.rng.uniform(0.85, 1.25), 3))))}
                       for (c, m), v in sorted(spend.items()) if c != "Uncategorized"]
        sids = [s["supplier_id"] for s in self.suppliers]
        t["user_supplier_access"] = [{"user_name": u, "supplier_id": s} for u, chunk in
                                     (("analyst_a", sids[:8]), ("analyst_b", sids[8:16]), ("analyst_c", sids[16:])) for s in chunk]
        t["_labels"] = [{"invoice_id": l["_inv"].invoice_id, "anomaly_type": l["anomaly_type"],
                         "expected_code": l["expected_code"], "note": l["note"]}
                        for l in sorted(self.labels, key=lambda l: (l["_inv"].invoice_id, l["anomaly_type"]))]
        return t

    def write(self, out: Path) -> dict:
        self.build_clean()
        self.inject()
        self.finalize()
        tables = self.tables()
        out.mkdir(parents=True, exist_ok=True)
        (out / "labels").mkdir(exist_ok=True)
        report = {"seed": self.seed, "n_po_requested": self.n_po, "per_type": self.per_type, "as_of": str(self.as_of),
                  "threshold": str(self.threshold), "rows": {}, "sha256": {}}
        for name, rows in tables.items():
            path = (out / "labels" / "anomaly_labels.csv") if name == "_labels" else out / f"{name}.csv"
            cols = list(rows[0].keys()) if rows else []
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=cols)
                w.writeheader()
                w.writerows(rows)
            report["rows"][name.lstrip("_")] = len(rows)
            report["sha256"][name.lstrip("_")] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        (out / "generation_report.json").write_text(json.dumps(report, indent=2))
        return report
