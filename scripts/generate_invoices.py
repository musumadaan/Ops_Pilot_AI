"""Generate synthetic invoice PDFs with exact JSON ground truth (ReportLab, no network).

    python scripts/generate_invoices.py --n 200 --seed 42 --out datasets/synthetic/invoices

Output
  <out>/pdf/INV_00001.pdf                digital PDFs, 4 layouts
  <out>/ground_truth/INV_00001.json      what is printed on the page (see "defect")
  datasets/synthetic/suppliers.csv       supplier master (supplier_id,name)

Defects: a share of invoices carry an arithmetic error ON PURPOSE (printed total or a line
amount is wrong). Ground truth records the *printed* values plus ``expected_flags`` so the
benchmark can measure whether validators catch them. All names are invented.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

CENT = Decimal("0.01")
W, H = LETTER

SUPPLIERS = [
    "Prairie Steel & Fabrication Ltd.", "Bowline Office Products Inc.", "Cedar Ridge Logistics Corp.",
    "Summit Industrial Fasteners Ltd.", "Glacier Peak Packaging Inc.", "Maple Leaf Safety Supply Ltd.",
    "Aurora Electrical Components Inc.", "Foothills Hydraulic Services Ltd.", "Tundra Cleaning Supplies Corp.",
    "Redwood IT Hardware Ltd.", "Lakeshore Printing & Signs Inc.", "Granite Tool & Die Ltd.",
    "Boreal Chemical Distribution Inc.", "Cobalt Fleet Maintenance Ltd.", "Harborview Catering Services Corp.",
    "Ironwood Building Materials Ltd.", "Skyline Courier Services Inc.", "Pinecrest Lab Equipment Ltd.",
    "Northern Lights Cabling Inc.", "Silverbirch Furniture Co.", "Copperfield Welding Supplies Ltd.",
    "Blue Heron Software Licensing Inc.", "Stonebridge Pallet & Crate Ltd.", "Windrow Agricultural Parts Corp.",
]
STREETS = ["Industrial Rd", "Commerce Way", "Mill St", "Railway Ave", "Harbour Dr", "Maple Blvd", "King St"]
CITIES = [("Calgary", "AB"), ("Edmonton", "AB"), ("Vancouver", "BC"), ("Regina", "SK"), ("Winnipeg", "MB"),
          ("Toronto", "ON"), ("Halifax", "NS")]
CATALOGUE = [
    ("Hex bolts M8 x 40mm box of 100", 18, 42), ("Hydraulic hose 3/4 in per metre", 9, 35),
    ("Safety gloves nitrile case", 40, 95), ("LED panel light 40W", 22, 65), ("Cable ties 300mm pack", 6, 15),
    ("Thermal printer labels roll", 7, 19), ("Pallet wrap 18 in roll", 16, 38), ("Steel plate 6mm per sheet", 120, 410),
    ("Cat6 patch cable 2m", 3, 9), ("Toner cartridge black", 70, 190), ("Industrial degreaser 20L", 55, 140),
    ("Welding rods 3.2mm box", 28, 76), ("Hard hat class E", 14, 39), ("Forklift battery service", 180, 640),
    ("Laptop docking station", 110, 260), ("Ergonomic chair", 150, 480), ("Courier service per shipment", 12, 85),
    ("Calibration of pressure gauge", 60, 210), ("Pump seal kit", 45, 160), ("Cutting disc 125mm pack of 25", 24, 58),
    ("Software licence annual seat", 90, 360), ("Cleaning wipes tub", 5, 14), ("Rubber gasket 50mm", 2, 8),
    ("Preventive maintenance visit", 200, 780), ("Safety signage set", 25, 90), ("Drill bit set 19pc", 20, 70),
]
TAXES = [("GST", Decimal("0.05")), ("HST", Decimal("0.13")), ("VAT", Decimal("0.20")), ("Tax", Decimal("0.00"))]
CURRENCIES = ["CAD", "CAD", "CAD", "USD"]
BUYER = "OpsPilot Demo Buyer Corp."


def q(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def money(x: Decimal) -> str:
    return f"{x:,.2f}"


@dataclass
class Item:
    sku: str
    description: str
    quantity: int
    unit_price: Decimal
    amount: Decimal      # as printed (may be wrong on defect invoices)


def make_invoice(rng: random.Random, idx: int, layout: str) -> dict:
    supplier = rng.choice(SUPPLIERS)
    city, prov = rng.choice(CITIES)
    n_lines = rng.randint(12, 22) if rng.random() < 0.25 else rng.randint(2, 8)
    items: list[Item] = []
    for _ in range(n_lines):
        desc, lo, hi = rng.choice(CATALOGUE)
        qty = rng.randint(1, 40)
        price = q(Decimal(str(rng.uniform(lo, hi))))
        items.append(Item(f"{rng.choice(['HW','EL','SV','OF','CH'])}-{rng.randint(1000, 9999)}",
                          desc, qty, price, q(price * qty)))
    subtotal = q(sum((i.amount for i in items), Decimal(0)))
    tax_name, rate = rng.choice(TAXES)
    tax = q(subtotal * rate)
    total = subtotal + tax
    inv_date = date(2026, 1, 1) + timedelta(days=rng.randint(0, 270))
    due = inv_date + timedelta(days=rng.choice([14, 30, 45, 60]))
    defect, expected = None, []
    r = rng.random()
    if r < 0.12:
        defect, expected = "total_mismatch", ["total_mismatch"]
        total = total + q(Decimal(str(rng.uniform(5, 400))))
    elif r < 0.22 and len(items) >= 2:
        defect, expected = "line_amount_mismatch", ["line_amount_mismatch", "subtotal_mismatch"]
        k = rng.randrange(len(items))
        items[k].amount = items[k].amount + q(Decimal(str(rng.uniform(5, 150))))
    return {
        "id": f"INV_{idx:05d}", "layout": layout, "supplier_name": supplier,
        "address": [f"{rng.randint(100, 9999)} {rng.choice(STREETS)}", f"{city}, {prov}"],
        "invoice_number": f"INV-2026-{rng.randint(1, 999999):06d}",
        "invoice_date": inv_date, "due_date": due,
        "po_number": f"PO-{rng.randint(10000, 99999)}",
        "currency": rng.choice(CURRENCIES), "tax_name": tax_name, "tax_rate": rate,
        "items": items, "subtotal": subtotal, "tax": tax, "total": total,
        "defect": defect, "expected_flags": expected,
    }


DATE_FMT = {
    "classic": lambda d: d.strftime("%Y-%m-%d"),
    "grid": lambda d: d.strftime("%b %d, %Y"),
    "sku": lambda d: d.strftime("%Y/%m/%d"),
    "compact": lambda d: d.strftime("%d %b %Y"),
}


# ------------------------------------------------------------------ drawing helpers
class Pen:
    def __init__(self, c: canvas.Canvas):
        self.c = c

    def text(self, x, y, s, font="Helvetica", size=10, color=black, align="l"):
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        {"l": self.c.drawString, "r": self.c.drawRightString, "c": self.c.drawCentredString}[align](x, y, s)


def tax_label(inv: dict) -> str:
    return f"{inv['tax_name']} ({inv['tax_rate'] * 100:.0f}%)"


def draw_classic(c: canvas.Canvas, inv: dict) -> None:
    p, fd, ccy = Pen(c), DATE_FMT["classic"], inv["currency"]
    p.text(50, H - 60, inv["supplier_name"], "Helvetica-Bold", 16)
    p.text(50, H - 76, inv["address"][0]); p.text(50, H - 89, inv["address"][1])
    p.text(W - 50, H - 60, "INVOICE", "Helvetica-Bold", 24, align="r")
    y = H - 120
    for label, val in (("Invoice No:", inv["invoice_number"]), ("Invoice Date:", fd(inv["invoice_date"])),
                       ("Due Date:", fd(inv["due_date"])), ("PO Number:", inv["po_number"])):
        p.text(W - 200, y, label, "Helvetica-Bold"); p.text(W - 50, y, val, align="r"); y -= 15
    p.text(50, H - 130, "Bill To:", "Helvetica-Bold"); p.text(50, H - 144, BUYER)
    y = H - 215
    for x, lab, al in ((50, "Description", "l"), (340, "Qty", "r"), (430, "Unit Price", "r"), (W - 50, "Amount", "r")):
        p.text(x, y, lab, "Helvetica-Bold", align=al)
    c.line(50, y - 5, W - 50, y - 5); y -= 20
    for it in inv["items"]:
        p.text(50, y, it.description[:52]); p.text(340, y, str(it.quantity), align="r")
        p.text(430, y, money(it.unit_price), align="r"); p.text(W - 50, y, money(it.amount), align="r"); y -= 15
    c.line(50, y + 8, W - 50, y + 8); y -= 14
    for lab, val in (("Subtotal", inv["subtotal"]), (tax_label(inv), inv["tax"]), (f"Total Due ({ccy})", inv["total"])):
        bold = "Helvetica-Bold" if lab.startswith("Total") else "Helvetica"
        p.text(W - 170, y, lab, bold, align="r"); p.text(W - 50, y, money(val), bold, align="r"); y -= 16
    p.text(50, 60, "Payment terms: net 30. Thank you for your business.", size=8)


def draw_grid(c: canvas.Canvas, inv: dict) -> None:
    p, fd, ccy = Pen(c), DATE_FMT["grid"], inv["currency"]
    p.text(W / 2, H - 55, "TAX INVOICE", "Helvetica-Bold", 20, align="c")
    p.text(50, H - 90, "From:", "Helvetica-Bold"); p.text(95, H - 90, inv["supplier_name"], "Helvetica-Bold", 12)
    p.text(95, H - 104, inv["address"][0]); p.text(95, H - 117, inv["address"][1])
    c.rect(330, H - 150, W - 380, 70)
    y = H - 98
    for label, val in (("Invoice Number:", inv["invoice_number"]), ("Invoice Date:", fd(inv["invoice_date"])),
                       ("Due Date:", fd(inv["due_date"])), ("Purchase Order:", inv["po_number"])):
        p.text(338, y, label, size=9); p.text(W - 56, y, val, size=9, align="r"); y -= 14
    p.text(50, H - 150, f"Currency: {ccy}", size=9)
    top = H - 190
    cols = [50, 78, 330, 385, 460, W - 50]
    c.setFillColor(HexColor("#dde6f0")); c.rect(50, top - 4, W - 100, 18, fill=1, stroke=0)
    heads = ["#", "Item Description", "Qty", "Rate", "Total"]
    for i, hd in enumerate(heads):
        x = cols[i] + 4 if i < 2 else cols[i + 1] - 4
        p.text(x, top, hd, "Helvetica-Bold", 9, align="l" if i < 2 else "r")
    y = top - 18
    for n, it in enumerate(inv["items"], 1):
        c.rect(50, y - 4, W - 100, 15)
        p.text(54, y, str(n), size=9); p.text(82, y, it.description[:50], size=9)
        p.text(cols[3] - 4, y, str(it.quantity), size=9, align="r")
        p.text(cols[4] - 4, y, money(it.unit_price), size=9, align="r")
        p.text(cols[5] - 4, y, money(it.amount), size=9, align="r"); y -= 15
    y -= 14
    for lab, val in (("Sub-total", inv["subtotal"]), (tax_label(inv), inv["tax"]), ("Amount Due", inv["total"])):
        bold = "Helvetica-Bold" if lab == "Amount Due" else "Helvetica"
        p.text(W - 150, y, lab, bold, 10, align="r"); p.text(W - 54, y, money(val), bold, 10, align="r"); y -= 16


def draw_sku(c: canvas.Canvas, inv: dict) -> None:
    p, fd, ccy = Pen(c), DATE_FMT["sku"], inv["currency"]
    c.setFillColor(HexColor("#17365D")); c.rect(0, H - 90, W, 90, fill=1, stroke=0)
    p.text(50, H - 52, inv["supplier_name"], "Helvetica-Bold", 17, white)
    p.text(50, H - 70, f"{inv['address'][0]}, {inv['address'][1]}", size=9, color=white)
    y = H - 125
    for label, val in (("Invoice #", inv["invoice_number"]), ("Date", fd(inv["invoice_date"])),
                       ("Payment due", fd(inv["due_date"])), ("PO No.", inv["po_number"]), ("Currency", ccy)):
        p.text(50, y, label, "Helvetica-Bold", 10); p.text(150, y, val); y -= 15
    top = y - 20
    for x, lab, al in ((50, "SKU", "l"), (135, "Description", "l"), (385, "Qty", "r"), (460, "Unit Price", "r"), (W - 50, "Amount", "r")):
        p.text(x, top, lab, "Helvetica-Bold", 9, align=al)
    c.line(50, top - 5, W - 50, top - 5); y = top - 20
    for it in inv["items"]:
        p.text(50, y, it.sku, size=9); p.text(135, y, it.description[:44], size=9)
        p.text(385, y, str(it.quantity), size=9, align="r"); p.text(460, y, money(it.unit_price), size=9, align="r")
        p.text(W - 50, y, money(it.amount), size=9, align="r"); y -= 15
    y -= 12
    for lab, val in (("Subtotal", inv["subtotal"]), (tax_label(inv), inv["tax"]), ("Grand Total", inv["total"])):
        bold = "Helvetica-Bold" if lab == "Grand Total" else "Helvetica"
        p.text(W - 150, y, lab, bold, align="r"); p.text(W - 50, y, money(val), bold, align="r"); y -= 16


def draw_compact(c: canvas.Canvas, inv: dict) -> None:
    p, fd = Pen(c), DATE_FMT["compact"]
    p.text(40, H - 50, inv["supplier_name"], "Helvetica-Bold", 14)
    p.text(40, H - 63, f"{inv['address'][0]}, {inv['address'][1]}", size=8)
    p.text(W - 40, H - 50, "Invoice", "Helvetica", 18, align="r")
    y = H - 95
    for label, val in (("Invoice Number:", inv["invoice_number"]), ("Date of issue:", fd(inv["invoice_date"])),
                       ("Payment due:", fd(inv["due_date"])), ("Purchase Order:", inv["po_number"]),
                       ("Currency:", inv["currency"])):
        p.text(40, y, label, "Helvetica-Bold", 9); p.text(130, y, val, size=9); y -= 12
    top = y - 18
    for x, lab, al in ((40, "Description", "l"), (350, "Qty", "r"), (430, "Unit price", "r"), (W - 40, "Amount", "r")):
        p.text(x, top, lab, "Helvetica-Bold", 8.5, align=al)
    c.line(40, top - 4, W - 40, top - 4); y = top - 16
    for it in inv["items"]:
        words = it.description.split()
        mid = len(words) // 2 if len(it.description) > 34 else len(words)
        first, second = " ".join(words[:mid]), " ".join(words[mid:])
        p.text(40, y, first, size=8.5); p.text(350, y, str(it.quantity), size=8.5, align="r")
        p.text(430, y, money(it.unit_price), size=8.5, align="r"); p.text(W - 40, y, money(it.amount), size=8.5, align="r")
        y -= 11
        if second:
            p.text(40, y, second, size=8.5); y -= 11
        y -= 3
    y -= 8
    for lab, val in (("Subtotal", inv["subtotal"]), (tax_label(inv), inv["tax"]), ("Total", inv["total"])):
        bold = "Helvetica-Bold" if lab == "Total" else "Helvetica"
        p.text(W - 130, y, lab, bold, 9, align="r"); p.text(W - 40, y, money(val), bold, 9, align="r"); y -= 13


LAYOUTS = {"classic": draw_classic, "grid": draw_grid, "sku": draw_sku, "compact": draw_compact}


def ground_truth(inv: dict) -> dict:
    return {
        "id": inv["id"], "layout": inv["layout"], "supplier_name": inv["supplier_name"],
        "invoice_number": inv["invoice_number"], "invoice_date": inv["invoice_date"].isoformat(),
        "due_date": inv["due_date"].isoformat(), "po_number": inv["po_number"], "currency": inv["currency"],
        "subtotal": str(inv["subtotal"]), "tax": str(inv["tax"]), "total": str(inv["total"]),
        "line_items": [{"sku": it.sku if inv["layout"] == "sku" else None, "description": it.description,
                        "quantity": str(it.quantity), "unit_price": str(it.unit_price), "amount": str(it.amount)}
                       for it in inv["items"]],
        "n_lines": len(inv["items"]), "table_heavy": len(inv["items"]) >= 12,
        "defect": inv["defect"], "expected_flags": inv["expected_flags"],
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="datasets/synthetic/invoices")
    ap.add_argument("--suppliers-csv", default="datasets/synthetic/suppliers.csv")
    a = ap.parse_args(argv)

    rng = random.Random(a.seed)
    out = Path(a.out)
    (out / "pdf").mkdir(parents=True, exist_ok=True)
    (out / "ground_truth").mkdir(parents=True, exist_ok=True)
    layouts = list(LAYOUTS)
    for i in range(1, a.n + 1):
        layout = layouts[(i - 1) % len(layouts)]
        inv = make_invoice(rng, i, layout)
        pdf = out / "pdf" / f"{inv['id']}.pdf"
        c = canvas.Canvas(str(pdf), pagesize=LETTER, invariant=1)  # invariant => reproducible bytes
        c.setTitle(inv["id"]); LAYOUTS[layout](c, inv); c.save()
        (out / "ground_truth" / f"{inv['id']}.json").write_text(json.dumps(ground_truth(inv), indent=2))
    sup = Path(a.suppliers_csv)
    sup.parent.mkdir(parents=True, exist_ok=True)
    with open(sup, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh); wr.writerow(["supplier_id", "name"])
        for k, name in enumerate(SUPPLIERS, 1):
            wr.writerow([f"SUP{k:04d}", name])
    print(f"wrote {a.n} invoices to {out} and {len(SUPPLIERS)} suppliers to {sup}")


if __name__ == "__main__":
    main()
