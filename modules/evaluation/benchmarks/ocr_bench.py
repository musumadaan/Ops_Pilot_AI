"""OCR benchmark: engines x preprocessing x degradation strata, scored against ground truth.

    python -m modules.evaluation.benchmarks.ocr_bench \
        --manifest datasets/evaluation/ocr/manifest.csv --split test \
        --configs tesseract_psm6:default tesseract_psm6:none tesseract_psm4:default \
        --out modules/evaluation/reports

Config syntax is ``engine:preprocess``. The ``digital`` stratum uses the PDF text layer (no
OCR) and acts as the upper bound for the extraction rules themselves. Writes a JSON file with
per-document results and a Markdown summary table. Record the machine and conditions when you
publish numbers (Section 11: no made-up percentages, always report sample size).
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import statistics as st
import time
from collections import defaultdict
from pathlib import Path

from modules.ocr.pipeline import DocumentPipeline

from ..metrics.field_match import ALL_FIELDS, DocScore, prf, score


def _load(manifest: str, split: str, strata: set[str] | None, limit: int) -> list[dict]:
    with open(manifest, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows = [r for r in rows if split == "all" or r["split"] == split]
    if strata:
        rows = [r for r in rows if r["stratum"] in strata]
    return rows[:limit] if limit else rows


def run(manifest, split, configs, strata, limit, suppliers=None):
    from modules.ocr.validate.supplier_match import SupplierMaster

    master = SupplierMaster.from_csv(suppliers) if suppliers else None
    rows = _load(manifest, split, strata, limit)
    results = []
    for cfg in configs:
        engine, _, prep = cfg.partition(":")
        prep = prep or "default"
        for r in rows:
            digital = r["stratum"] == "digital"
            if digital and cfg != configs[0]:
                continue  # the text-layer path does not depend on engine/preprocess
            pipe = DocumentPipeline(engine=None if digital else engine, preprocess_cfg=prep,
                                    supplier_master=master)
            gt = json.loads(Path(r["gt"]).read_text())
            t0 = time.perf_counter()
            try:
                inv = pipe.process(r["pdf"] if digital else r["image"])
                sc = score(inv, gt)
                err = None
            except Exception as exc:  # a crash is a result, not a reason to stop the benchmark
                sc, err = DocScore(defect=gt.get("defect")), f"{type(exc).__name__}: {exc}"
            results.append({"config": "pdf_text" if digital else cfg, "stratum": r["stratum"], "id": r["id"],
                            "layout": r["layout"], "table_heavy": r["table_heavy"] == "True",
                            "seconds": round(time.perf_counter() - t0, 3), "error": err, "score": sc})
    return results


def summarise(results: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in results:
        groups[(r["config"], r["stratum"])].append(r)
    out = []
    for (cfg, stratum), rs in sorted(groups.items()):
        scs = [r["score"] for r in rs]
        n = len(rs)
        field_acc = {f: sum(s.fields.get(f, False) for s in scs) / n for f in ALL_FIELDS}
        tp, fp, fn = (sum(getattr(s, k) for s in scs) for k in ("line_tp", "line_fp", "line_fn"))
        p, rec, f1 = prf(tp, fp, fn)
        defects = [s for s in scs if s.defect_caught is not None]
        clean = [s for s in scs if s.false_arith_flag is not None]
        out.append({
            "config": cfg, "stratum": stratum, "n": n, "errors": sum(1 for r in rs if r["error"]),
            "field_exact_mean": sum(field_acc.values()) / len(field_acc),
            "field_exact": field_acc, "line_f1": f1, "line_precision": p, "line_recall": rec,
            "defect_recall": (sum(s.defect_caught for s in defects) / len(defects)) if defects else None,
            "n_defects": len(defects),
            "false_arith_flag_rate": (sum(s.false_arith_flag for s in clean) / len(clean)) if clean else None,
            "review_rate": sum(s.needs_review for s in scs) / n,
            "median_seconds": st.median(r["seconds"] for r in rs),
        })
    return out


def to_markdown(summary: list[dict]) -> str:
    pct = lambda v: "-" if v is None else f"{100 * v:.1f}%"
    lines = [
        f"Machine: {platform.platform()} | Python {platform.python_version()}",
        "",
        "| config | stratum | n | fields exact | line F1 | defect recall | false arith flags | review rate | median s |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in summary:
        lines.append(f"| {s['config']} | {s['stratum']} | {s['n']} | {pct(s['field_exact_mean'])} | "
                     f"{pct(s['line_f1'])} | {pct(s['defect_recall'])} (n={s['n_defects']}) | "
                     f"{pct(s['false_arith_flag_rate'])} | {pct(s['review_rate'])} | {s['median_seconds']:.2f} |")
    lines += ["", "Per-field exact match:", "", "| config | stratum | " + " | ".join(ALL_FIELDS) + " |",
              "|---|---|" + "---:|" * len(ALL_FIELDS)]
    for s in summary:
        lines.append(f"| {s['config']} | {s['stratum']} | " + " | ".join(pct(s["field_exact"][f]) for f in ALL_FIELDS) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="datasets/evaluation/ocr/manifest.csv")
    ap.add_argument("--split", default="test", choices=["test", "dev", "all"])
    ap.add_argument("--configs", nargs="+", default=["tesseract_psm6:default"])
    ap.add_argument("--strata", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--suppliers", default=None)
    ap.add_argument("--out", default="modules/evaluation/reports")
    a = ap.parse_args(argv)

    results = run(a.manifest, a.split, a.configs, set(a.strata) if a.strata else None, a.limit, a.suppliers)
    summary = summarise(results)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    ser = [{**r, "score": r["score"].__dict__} for r in results]
    (out / f"ocr_bench_{stamp}.json").write_text(json.dumps({"summary": summary, "results": ser}, indent=2, default=str))
    md = to_markdown(summary)
    (out / f"ocr_bench_{stamp}.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
