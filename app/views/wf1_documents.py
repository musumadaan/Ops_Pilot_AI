import json
import shutil
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from app import common as c

PDFS = c.ROOT / "datasets/synthetic/invoices/pdf"
TRUTH = c.ROOT / "datasets/synthetic/invoices/ground_truth"


@st.cache_resource(show_spinner=False)
def pipeline(force_ocr: bool):
    from modules.ocr.pipeline import DocumentPipeline
    from modules.ocr.validate.supplier_match import SupplierMaster
    engine = "tesseract" if shutil.which("tesseract") else None
    return DocumentPipeline(engine=engine, supplier_master=SupplierMaster.from_csv(c.SUPPLIERS), force_ocr=force_ocr)


def _fields(inv) -> pd.DataFrame:
    rows = []
    for name in ("supplier_name", "invoice_number", "invoice_date", "due_date", "po_number", "currency", "subtotal", "tax", "total"):
        f = getattr(inv, name)
        rows.append({"field": name, "value": None if f is None else str(f.value), "confidence": None if f is None else round(f.confidence, 2),
                     "read as": None if f is None else f.raw, "source": None if f is None else f.source})
    return pd.DataFrame(rows)


def _compare(inv, gt: dict) -> pd.DataFrame:
    rows = []
    for name in ("invoice_number", "invoice_date", "due_date", "po_number", "currency", "subtotal", "tax", "total"):
        f = getattr(inv, name)
        got = None if f is None else str(f.value)
        want = None if gt.get(name) is None else str(gt[name])
        ok = got == want or (got and want and _num(got) == _num(want))
        rows.append({"field": name, "truth": want, "extracted": got, "match": "yes" if ok else "no"})
    return pd.DataFrame(rows)


def _num(s: str):
    try:
        return round(float(s.replace(",", "")), 2)
    except ValueError:
        return s


def render() -> None:
    c.page_header("Document intelligence", "Turns an invoice PDF or scan into structured fields, each with a confidence and a box on the page, then "
                  "checks the arithmetic. Anything doubtful is flagged for a person instead of being guessed.", "Workflow 1")
    have_ocr = bool(shutil.which("tesseract"))
    src = st.radio("Source", ["Sample invoice", "Upload your own"], horizontal=True)
    path: Path | None = None
    gt = None
    if src == "Sample invoice":
        files = sorted(PDFS.glob("*.pdf"))
        if not files:
            st.warning("No sample PDFs found. Run `python scripts/generate_invoices.py` first.")
            return
        pick = st.selectbox("Invoice", [f.stem for f in files[:200]], index=0)
        path = PDFS / f"{pick}.pdf"
        if (TRUTH / f"{pick}.json").exists():
            gt = json.loads((TRUTH / f"{pick}.json").read_text())
    else:
        up = st.file_uploader("PDF or image", type=["pdf", "png", "jpg", "jpeg", "tif", "tiff"])
        if up:
            tmp = Path(tempfile.mkdtemp()) / up.name
            tmp.write_bytes(up.getvalue())
            path = tmp
    force = st.toggle("Force OCR (rasterise the page and read it like a scan)", value=False, disabled=not have_ocr,
                      help="Needs the Tesseract program. Without it only PDFs with a text layer can be read.")
    if not have_ocr:
        st.info("Tesseract is not installed here, so only PDFs with a text layer can be processed. On a Mac: `brew install tesseract`.")
    if path is None:
        return
    with st.spinner("Reading the document..."):
        try:
            inv = pipeline(force).process(path)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not process this file: {exc}")
            return

    left, right = st.columns([1, 1.15])
    with left:
        st.markdown("**Page with evidence boxes**")
        try:
            import cv2

            from modules.ocr.engines.pdf_text import render_pdf
            from modules.ocr.overlay import draw_overlay
            pages = render_pdf(path, 110) if path.suffix.lower() == ".pdf" else [cv2.imread(str(path))]
            img = draw_overlay(inv, pages)[0] if st.toggle("Show boxes", value=True) else pages[0]
            st.image(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), width="stretch")
        except Exception as exc:  # noqa: BLE001
            st.caption(f"Preview unavailable: {exc}")
    with right:
        p = inv.processing
        a, b, d = st.columns(3)
        a.metric("Engine", p.engine)
        b.metric("Used OCR", "yes" if p.used_ocr else "no")
        d.metric("Time", f"{p.elapsed_seconds:.2f}s")
        st.markdown("**Extracted fields**")
        st.dataframe(_fields(inv), hide_index=True, width="stretch")
        if inv.supplier_match:
            m = inv.supplier_match
            st.markdown(f"Supplier match: **{m.master_name or 'unknown'}** ({m.supplier_id or '-'}), score {m.score:.0f}, status {m.status}")
        st.markdown("**Checks**")
        if inv.flags:
            for f in inv.flags:
                st.markdown(c.pill(f.severity, "error" if f.severity == "error" else "warning" if f.severity == "warning" else "info") + f"`{f.code}` {f.message}",
                            unsafe_allow_html=True)
        else:
            st.markdown(c.pill("all checks passed", "ok"), unsafe_allow_html=True)

    st.markdown("**Line items**")
    st.dataframe(pd.DataFrame([{"#": li.line_no, "description": li.description, "qty": li.quantity, "unit price": li.unit_price, "amount": li.amount,
                                "confidence": round(li.confidence, 2)} for li in inv.line_items]), hide_index=True, width="stretch")
    if gt:
        st.markdown("**Compared with the known answer**")
        cmp_ = _compare(inv, gt)
        st.dataframe(cmp_, hide_index=True, width="stretch")
        st.caption(f"{(cmp_['match'] == 'yes').sum()} of {len(cmp_)} fields match. Injected defect in this sample: **{gt.get('defect') or 'none'}**.")
    with st.expander("Raw JSON"):
        st.json(json.loads(inv.model_dump_json()))