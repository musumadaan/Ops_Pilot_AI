"""HTTP service for Workflow 1 (FastAPI). Optional dependency: ``pip install fastapi python-multipart``.

    uvicorn modules.ocr.api:app --port 8001
    curl -F file=@invoice.pdf http://localhost:8001/ocr/extract

Upload handling is defensive: size cap, magic-byte type check (the filename and Content-Type
header are not trusted), random temp file name, and the temp file is always removed.
The service only *reads* documents; it never writes to any business system.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

from .extract.invoice_schema import InvoiceExtraction
from .pipeline import DocumentPipeline
from .validate.supplier_match import SupplierMaster

MAX_BYTES = int(os.getenv("OCR_MAX_UPLOAD_BYTES", 15 * 1024 * 1024))
SUPPLIERS_CSV = os.getenv("OCR_SUPPLIERS_CSV", "datasets/synthetic/suppliers.csv")
ENGINE = os.getenv("OCR_ENGINE", "tesseract")

# (magic prefix, file suffix)
_MAGIC = [(b"%PDF-", ".pdf"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"),
          (b"II*\x00", ".tif"), (b"MM\x00*", ".tif")]


def detect_suffix(head: bytes) -> str | None:
    for magic, suffix in _MAGIC:
        if head.startswith(magic):
            return suffix
    return None


def _master() -> SupplierMaster | None:
    return SupplierMaster.from_csv(SUPPLIERS_CSV) if Path(SUPPLIERS_CSV).exists() else None


app = FastAPI(title="OpsPilot OCR service", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "engine": ENGINE}


@app.post("/ocr/extract", response_model=InvoiceExtraction)
async def extract(file: UploadFile = File(...)) -> InvoiceExtraction:
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"File larger than {MAX_BYTES} bytes")
    if not data:
        raise HTTPException(400, "Empty file")
    suffix = detect_suffix(data[:16])
    if suffix is None:
        raise HTTPException(415, "Unsupported file type (PDF, PNG, JPEG or TIFF only)")

    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        # fresh pipeline per request: duplicate tracking is a data-layer concern (Workflow 2)
        pipe = DocumentPipeline(engine=ENGINE, supplier_master=_master())
        inv = pipe.process(tmp)
    except HTTPException:
        raise
    except Exception as exc:  # corrupt PDF, unreadable image, engine failure
        raise HTTPException(422, f"Could not process document: {type(exc).__name__}") from exc
    finally:
        Path(tmp).unlink(missing_ok=True)
    inv.source_file = Path(file.filename or "upload").name
    return inv
