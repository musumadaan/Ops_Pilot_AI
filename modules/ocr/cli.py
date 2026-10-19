"""Command line: extract one document, draw an overlay, or record a correction.

    python -m modules.ocr.cli extract invoice.pdf --out out.json --overlay out.png
    python -m modules.ocr.cli extract scan.png --engine tesseract_psm4 --preprocess full
    python -m modules.ocr.cli extract scan.png --suppliers datasets/synthetic/suppliers.csv
    python -m modules.ocr.cli correct out.json total 1234.50 --reviewer muskan
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .extract.invoice_schema import InvoiceExtraction
from .overlay import overlay_file
from .pipeline import DocumentPipeline
from .review.corrections import Correction, apply_corrections, save_correction
from .validate.supplier_match import SupplierMaster


def _cmd_extract(a: argparse.Namespace) -> int:
    master = SupplierMaster.from_csv(a.suppliers) if a.suppliers else None
    pipe = DocumentPipeline(engine=a.engine, preprocess_cfg=a.preprocess, supplier_master=master,
                            dpi=a.dpi, force_ocr=a.force_ocr, day_first=a.day_first)
    inv = pipe.process(a.path)
    text = inv.model_dump_json(indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    if a.overlay:
        overlay_file(inv, a.path, a.overlay)
    codes = ", ".join(sorted(inv.flag_codes())) or "none"
    print(f"[{inv.processing.engine}] needs_review={inv.needs_review} flags: {codes} "
          f"({inv.processing.elapsed_seconds}s)", file=sys.stderr)
    return 1 if inv.needs_review and a.fail_on_review else 0


def _cmd_correct(a: argparse.Namespace) -> int:
    inv = InvoiceExtraction.model_validate_json(Path(a.extraction).read_text(encoding="utf-8"))
    old = getattr(inv, a.field, None)
    c = Correction(inv.source_sha256, a.field, getattr(old, "raw", None), a.value, a.reviewer)
    save_correction(c, a.store)
    fixed = apply_corrections(inv, [c])
    pipe = DocumentPipeline(engine=None)
    fixed = pipe.revalidate(fixed)
    Path(a.extraction).write_text(fixed.model_dump_json(indent=2), encoding="utf-8")
    print(f"Saved correction to {a.store}; needs_review={fixed.needs_review}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="opspilot-ocr")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="extract fields from a PDF or image")
    e.add_argument("path")
    e.add_argument("--engine", default="tesseract", help="tesseract | tesseract_psm4 | paddle | vision")
    e.add_argument("--preprocess", default="default", choices=["none", "default", "full"])
    e.add_argument("--dpi", type=int, default=300)
    e.add_argument("--force-ocr", action="store_true", help="ignore the PDF text layer")
    e.add_argument("--day-first", action="store_true", help="read 03/04/2026 as 3 April")
    e.add_argument("--suppliers", help="CSV with supplier_id,name columns")
    e.add_argument("--out", help="write JSON here instead of stdout")
    e.add_argument("--overlay", help="write an annotated PNG here")
    e.add_argument("--fail-on-review", action="store_true", help="exit 1 if needs_review")
    e.set_defaults(fn=_cmd_extract)

    c = sub.add_parser("correct", help="apply and store a human correction")
    c.add_argument("extraction", help="JSON written by 'extract'")
    c.add_argument("field")
    c.add_argument("value")
    c.add_argument("--reviewer", required=True)
    c.add_argument("--store", default="data/review/corrections.jsonl")
    c.set_defaults(fn=_cmd_correct)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
