"""Turn clean synthetic PDFs into scan-like images in four strata and write the manifest.

    python scripts/degrade_invoices.py --src datasets/synthetic/invoices \
        --manifest datasets/evaluation/ocr/manifest.csv [--augraphy]

Strata
  clean  : plain render at 200 dpi
  skew   : rotated 1.5-4.5 degrees + mild sensor noise
  lowres : ~90 dpi equivalent + heavy JPEG compression
  noisy  : blur + gaussian/salt-pepper noise + uneven lighting + low contrast
  augraphy (optional, --augraphy): Augraphy default pipeline, seeded
Every image is seeded from (seed, document id, stratum) so runs are reproducible.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.ocr.engines.pdf_text import render_pdf


def _rng(seed: int, doc: str, stratum: str) -> np.random.Generator:
    h = hashlib.sha256(f"{seed}|{doc}|{stratum}".encode()).digest()
    return np.random.default_rng(int.from_bytes(h[:8], "big"))


def _noise(img: np.ndarray, rng: np.random.Generator, sigma: float) -> np.ndarray:
    n = rng.normal(0, sigma, img.shape)
    return np.clip(img.astype(np.float32) + n, 0, 255).astype(np.uint8)


def _rotate(img: np.ndarray, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def make_clean(img, rng):
    return img


def make_skew(img, rng):
    angle = float(rng.uniform(1.5, 4.5) * rng.choice([-1, 1]))
    return _noise(_rotate(img, angle), rng, 5)


def make_lowres(img, rng):
    small = cv2.resize(img, None, fx=0.45, fy=0.45, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(30, 45))])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def make_noisy(img, rng):
    out = cv2.GaussianBlur(img, (3, 3), 0)
    out = _noise(out, rng, 16)
    h, w = out.shape[:2]
    mask = rng.random((h, w)) < 0.002
    out[mask] = 0
    grad = np.linspace(rng.uniform(0.78, 0.95), 1.0, w, dtype=np.float32)[None, :, None]
    out = np.clip(out.astype(np.float32) * grad, 0, 255)
    out = 255 - (255 - out) * 0.85  # lower contrast
    return out.astype(np.uint8)


def make_augraphy(img, rng):
    import random

    from augraphy import default_augraphy_pipeline

    seed = int(rng.integers(0, 2**31 - 1))
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    return default_augraphy_pipeline()(img)


STRATA = {"clean": make_clean, "skew": make_skew, "lowres": make_lowres, "noisy": make_noisy}


def split_of(doc_id: str) -> str:
    """Deterministic 30% test split by document id (no leakage across strata)."""
    return "test" if int(hashlib.sha256(doc_id.encode()).hexdigest(), 16) % 10 < 3 else "dev"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="datasets/synthetic/invoices")
    ap.add_argument("--manifest", default="datasets/evaluation/ocr/manifest.csv")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--augraphy", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="only the first N documents")
    a = ap.parse_args(argv)

    src = Path(a.src)
    pdfs = sorted((src / "pdf").glob("*.pdf"))[: a.limit or None]
    strata = dict(STRATA)
    if a.augraphy:
        strata["augraphy"] = make_augraphy
    rows = []
    for pdf in pdfs:
        doc = pdf.stem
        gt_path = src / "ground_truth" / f"{doc}.json"
        gt = json.loads(gt_path.read_text())
        base = render_pdf(pdf, a.dpi)[0]
        common = dict(id=doc, pdf=str(pdf), gt=str(gt_path), layout=gt["layout"], n_lines=gt["n_lines"],
                      table_heavy=gt["table_heavy"], split=split_of(doc), seed=a.seed)
        rows.append({**common, "stratum": "digital", "image": ""})
        for name, fn in strata.items():
            out = src / "degraded" / name / f"{doc}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out), fn(base.copy(), _rng(a.seed, doc, name)))
            rows.append({**common, "stratum": name, "image": str(out)})
    man = Path(a.manifest)
    man.parent.mkdir(parents=True, exist_ok=True)
    with open(man, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    print(f"wrote {len(rows)} manifest rows ({len(pdfs)} documents x {len(strata) + 1} strata) -> {man}")


if __name__ == "__main__":
    main()
