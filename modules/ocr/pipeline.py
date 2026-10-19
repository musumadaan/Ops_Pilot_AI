"""End-to-end Workflow 1 pipeline: file -> validated ``InvoiceExtraction``.

Path selection
  PDF with a usable text layer -> read text directly (no OCR, confidence 1.0)
  PDF without one / image      -> render -> preprocess -> OCR engine -> extract
Then: field extraction -> arithmetic checks -> sanity checks -> supplier match.
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from modules.common.hashing import sha256_file

from .engines import OcrEngine, get_engine
from .engines.base import OcrPage
from .engines.pdf_text import extract_text_layer, render_pdf
from .extract.field_extractor import FieldExtractor
from .extract.invoice_schema import InvoiceExtraction, ProcessingInfo, ValidationFlag
from .preprocess import PRESETS, PreprocessConfig, preprocess
from .validate.arithmetic import check_arithmetic
from .validate.checks import run_checks
from .validate.supplier_match import SupplierMaster, supplier_flags

PIPELINE_VERSION = "0.1.0"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
# Flags produced by the extractor itself; kept when re-validating after human corrections.
EXTRACTION_FLAG_CODES = {"currency_ambiguous", "currency_conflict", "date_ambiguous"}


class DocumentPipeline:
    def __init__(
        self,
        engine: OcrEngine | str | None = "tesseract",
        preprocess_cfg: PreprocessConfig | str = "default",
        supplier_master: SupplierMaster | None = None,
        dpi: int = 300,
        force_ocr: bool = False,
        day_first: bool = False,
    ) -> None:
        self.engine: OcrEngine | None = get_engine(engine) if isinstance(engine, str) else engine
        self.cfg = PRESETS[preprocess_cfg] if isinstance(preprocess_cfg, str) else preprocess_cfg
        self.suppliers = supplier_master
        self.dpi, self.force_ocr = dpi, force_ocr
        self.extractor = FieldExtractor(day_first=day_first)
        self.seen_keys: set[str] = set()  # duplicate detection within one pipeline run

    # -------------------------------------------------------------- reading
    def _read_pages(self, path: Path) -> tuple[list[OcrPage], bool, list[str], str]:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            if not self.force_ocr:
                layer = extract_text_layer(path)
                if layer is not None:
                    return layer, False, ["pdf text layer (no OCR)"], "pdf_text"
            images = render_pdf(path, self.dpi)
        elif suffix in IMAGE_SUFFIXES:
            img = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError(f"Cannot decode image: {path}")
            images = [img]
        else:
            raise ValueError(f"Unsupported file type: {suffix}")
        if self.engine is None:
            raise RuntimeError("An OCR engine is required for scanned or image input")
        log: list[str] = []
        processed: list[np.ndarray] = []
        for i, img in enumerate(images, start=1):
            out, steps = preprocess(img, self.cfg)
            processed.append(out)
            log.extend(f"p{i}: {s}" for s in steps)
        return self.engine.run(processed), True, log, self.engine.name

    # -------------------------------------------------------------- main entry
    def process(self, path: str | Path) -> InvoiceExtraction:
        path = Path(path)
        t0 = time.perf_counter()
        pages, used_ocr, log, engine_name = self._read_pages(path)
        ext = self.extractor.extract(pages)

        inv = InvoiceExtraction(
            source_file=path.name,
            source_sha256=sha256_file(path),
            processing=ProcessingInfo(
                pipeline_version=PIPELINE_VERSION, engine=engine_name, used_ocr=used_ocr,
                preprocess=log, pages=len(pages), mean_word_confidence=ext.mean_conf,
            ),
            line_items=ext.line_items,
            flags=list(ext.flags),
            **ext.fields,  # type: ignore[arg-type]
        )
        if self.suppliers and inv.supplier_name:
            inv.supplier_match = self.suppliers.match(inv.supplier_name.value)
        inv.flags.extend(self._validate(inv, track_duplicates=True))
        inv.processing.elapsed_seconds = round(time.perf_counter() - t0, 3)
        return inv

    # -------------------------------------------------------------- validation
    def _validate(self, inv: InvoiceExtraction, track_duplicates: bool) -> list[ValidationFlag]:
        flags = check_arithmetic(inv)
        flags += run_checks(inv, seen_keys=self.seen_keys if track_duplicates else None)
        flags += supplier_flags(inv.supplier_match)
        return flags

    def revalidate(self, inv: InvoiceExtraction) -> InvoiceExtraction:
        """Re-run validators after human corrections; keeps extraction-time flags."""
        out = inv.model_copy(deep=True)
        if self.suppliers and out.supplier_name:
            out.supplier_match = self.suppliers.match(out.supplier_name.value)
        kept = [f for f in out.flags if f.code in EXTRACTION_FLAG_CODES]
        out.flags = kept + self._validate(out, track_duplicates=False)
        return out
