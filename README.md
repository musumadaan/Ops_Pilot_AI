# OpsPilot AI: Workflow 1 (document intelligence) and Workflow 2 (SQL and data engineering)

Workflow 1 is documented first; **Workflow 2 starts at the heading further down**.

Turns invoice PDFs and scans into a validated, source-linked `InvoiceExtraction` (Pydantic).
Every field carries value, confidence, page and bounding box. Failed checks raise explicit
flags and send the document to review; nothing is silently "fixed". All money is `Decimal`.

```
PDF with text layer ──► read text directly (no OCR, conf 1.0)
PDF scan / image ─────► render 300 dpi ► preprocess (upscale, deskew, ...) ► OCR engine
                                   │
                                   ▼
        field extraction (labels + layout rules) ► line-item table
                                   ▼
   arithmetic checks ► sanity checks ► supplier-master match ► duplicate check
                                   ▼
                 InvoiceExtraction  (needs_review = any error-level flag)
```

## Setup (Mac M4)

```bash
brew install tesseract poppler uv
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"            # core deps from pyproject.toml
uv pip install fastapi python-multipart uvicorn   # only for the HTTP service
# optional engines:  uv pip install -e ".[paddle]"   (CPU build)   |   -e ".[mac]"  (Apple Vision)
```

## Quick start

```bash
# 1. data: synthetic invoices + ground truth, then scan-like degraded copies + manifest
python scripts/generate_invoices.py --n 200 --seed 42 --out datasets/synthetic/invoices
python scripts/degrade_invoices.py  --src datasets/synthetic/invoices \
       --manifest datasets/evaluation/ocr/manifest.csv

# 2. extract one document (prints JSON; --overlay draws the boxes)
python -m modules.ocr.cli extract datasets/synthetic/invoices/degraded/skew/INV_00001.png \
       --suppliers datasets/synthetic/suppliers.csv --overlay /tmp/overlay.png

# 3. record a human correction and re-validate
python -m modules.ocr.cli correct out.json total 1234.50 --reviewer you

# 4. benchmark engines x preprocessing x strata -> modules/evaluation/reports/*.md|json
python -m modules.evaluation.benchmarks.ocr_bench --split test \
       --configs tesseract_psm6:default tesseract_psm6:none tesseract_psm4:default \
       --suppliers datasets/synthetic/suppliers.csv

# 5. HTTP service
uvicorn modules.ocr.api:app --port 8001
curl -F file=@invoice.pdf http://localhost:8001/ocr/extract

# 6. tests
pytest -m "not ocr"     # fast: parsing, validators, preprocessing, digital-PDF end-to-end, API
pytest -m ocr           # needs the tesseract binary: real OCR on the scanned strata
```

## Layout

| Path | Role |
|---|---|
| `modules/ocr/extract/invoice_schema.py` | The output contract (all later workflows depend on it) |
| `modules/ocr/extract/field_extractor.py`, `layout.py`, `parsing.py` | Labels, layout rules, line-item table, money/date parsing |
| `modules/ocr/engines/` | `pdf_text` (PyMuPDF), `tesseract`, `paddle`, `vision` (Apple Vision) behind one `OcrEngine` interface |
| `modules/ocr/preprocess.py` | Upscale, projection-profile deskew, optional denoise/binarise; every step logged |
| `modules/ocr/validate/` | Arithmetic (Decimal), required fields/dates/duplicates, supplier fuzzy match |
| `modules/ocr/review/corrections.py` | JSONL store of human corrections (future labelled data) |
| `modules/ocr/pipeline.py`, `cli.py`, `api.py`, `overlay.py` | Orchestration, CLI, FastAPI service, bounding-box overlay |
| `scripts/generate_invoices.py`, `degrade_invoices.py` | Synthetic invoices (4 layouts, deliberate arithmetic defects) and 4 scan strata |
| `modules/evaluation/` | Field exact-match, line-item P/R/F1, defect recall, benchmark runner |
| `tests/` | Unit and integration tests |

## Measured results (synthetic data, Linux x86 sandbox, Tesseract 5.3, test split)

Sample size is small (**n = 7 documents per stratum**), so treat these as a smoke test, not a
result to publish. Re-run on your M4 with the full 200-document set and record the machine.

| config | stratum | fields exact | line F1 | defect recall | review rate |
|---|---|---:|---:|---:|---:|
| pdf_text | digital | 100.0% | 100.0% | 100.0% (n=2) | 28.6% |
| tesseract_psm6:default | clean | 93.7% | 91.4% | 100.0% (n=2) | 71.4% |
| tesseract_psm6:default | lowres | 95.2% | 75.9% | 100.0% (n=2) | 71.4% |
| tesseract_psm6:default | skew | 95.2% | 79.0% | 100.0% (n=2) | 85.7% |
| tesseract_psm6:default | noisy | 87.3% | 69.4% | 100.0% (n=2) | 100.0% |

What this says: the extraction rules are exact on text-layer PDFs; on scans the weak spots are
the **invoice number** (57-86% exact) and **line items** (F1 69-91%), and the high review rate
is the safety net working (uncertain documents go to a person) but also a cost to reduce.
Next experiments: PaddleOCR and Apple Vision on the same strata, a full-set run, and checking
whether `tesseract_psm4` or `--preprocess full` helps the invoice-number field.

## Known limits

* `engines/paddle_engine.py` and `engines/vision_engine.py` were not executed in the build
  sandbox (no PaddlePaddle or macOS). Run them on the M4 and compare with the benchmark.
* Bare `$` is treated as ambiguous currency; a document with no ISO code gets a flag.
* Synthetic invoices test the design; they do not prove accuracy on real supplier documents.
* Do not use real invoices or personal data in this repository.


---

# Workflow 2: SQL and data engineering

PostgreSQL schema + migrations, a seeded synthetic procurement history with **labelled defects**, three-way-match and
exception rules in SQL, a parameterised read-only KPI service, data-quality checks, DuckDB local analytics, and a dbt project.

## Setup
```bash
brew install postgresql@16 && brew services start postgresql@16
createdb -E UTF8 -T template0 opspilot && createdb -E UTF8 -T template0 opspilot_test   # UTF8 matters: a SQL_ASCII cluster breaks the text data
cp .env.example .env && set -a && . ./.env && set +a
uv pip install -e ".[duckdb,dbt,dev]"
make migrate seed          # Alembic -> synthetic data -> Pandera validation -> COPY load
make dq                    # 16 data-quality rules
make test                  # whole suite (database tests skip if Postgres is unreachable)
make dbt                   # staging -> intermediate -> marts, with tests
```

## What is where
| Path | Purpose |
|---|---|
| `modules/analytics/sql/schema/` | DDL: 25 tables, CHECKs, partitioned append-only `audit_event`, row-level security, indexes, 13 exception codes |
| `modules/analytics/sql/views/` | `v_invoice_line_match` (window + LATERAL), `v_invoice_exceptions` (13 exception rules), approval chain (recursive CTE), `mv_monthly_spend`, view security |
| `modules/analytics/sql/kpi/` | 9 KPI templates with typed `:named` parameters |
| `modules/analytics/kpi_service.py` | The only SQL the agent layer may run: validated params, `READ ONLY` transaction, timeout, provenance (sql sha256, params, time) |
| `modules/analytics/synthetic.py` | Seeded generator; every injected defect is written to `labels/anomaly_labels.csv` |
| `modules/analytics/loader.py` | Pandera-validate, `TRUNCATE`, `COPY`, audit row, `ANALYZE` |
| `modules/analytics/load_extraction.py` | Workflow 1 `InvoiceExtraction` -> `ops.invoice` (refuses unmatched suppliers / missing fields) |
| `modules/analytics/reconcile.py` | Typed per-invoice reconciliation built from the views |
| `modules/analytics/duckdb_local.py` | Run the portable KPIs on DuckDB over the CSVs, no server |
| `modules/data_quality/` | Pandera raw-file schemas + 16 SQL consistency rules + CLI |
| `dbt/` | Sources, 5 staging, 1 intermediate, 4 marts, seeds, generic + singular tests |
| `scripts/explain_demo.py` | Before/after `EXPLAIN ANALYZE` on a 500k-row scratch table |

## Design decisions worth knowing
* **Business rules are data.** Tolerances and thresholds live in `ops.control_parameter`; changing one changes the views, no deploy.
* **Data quality is not business exceptions.** DQ asks "is the record consistent?" (`make dq`); exceptions ask "is the invoice a problem?" (`v_invoice_exceptions`).
* **No unique constraint on (supplier, invoice number)** on purpose: duplicates must be loadable so they can be detected.
* **Views are `security_invoker`**, so row-level security applies to whoever queries them, not the view owner.
* **The agent never writes SQL.** It picks a KPI id and parameters; this is what makes numbers auditable.

## Verified in this build (Linux sandbox, PostgreSQL 16, DuckDB 1.5, dbt-postgres 1.11)
* 113 tests pass (62 Workflow 1 + 51 Workflow 2).
* The SQL exception rules agree exactly with an **independent pandas re-implementation** (67 flagged invoice/code pairs, 0 differences) and detect all 44 labelled defects.
* The 5 DuckDB-portable KPIs match Postgres results; the other 4 use Postgres-only features and say so in their header.
* `dbt build`: 34 of 34 models and tests pass. Alembic downgrade/upgrade cycle works.

## Not done / limits
* **PySpark is not included** (optional in the blueprint; needs a JDK). Nothing here depends on it.
* Data is synthetic. The generator makes defects easy to label; real ERP data is messier, so treat recall figures as a test of the rules, not a forecast.
* `SPLIT_BELOW_THRESHOLD` and `POSSIBLE_DUPLICATE` are heuristics and can fire on legitimate invoices (the generator produces one such natural case).
* Faker is not used; the generator is plain seeded `random` for reproducibility.
* Tested on Linux x86; not run on macOS arm64 (pure Python + Postgres, so it should work, but it is unverified).


---

# Workflows 3-8

Run any of them from the repo root. Each has its own tests (`pytest tests/<name>`), and `python scripts/package_workflow.py <wf> --verify`
builds a self-contained folder for it (dependencies included) and runs its tests from a clean copy.

| WF | Module | Run | What it is |
|---|---|---|---|
| 3 | `modules/ml` | `python -m modules.ml.pipeline` | Leakage-safe invoice features, time-split anomaly models (logistic, boosted, isolation forest), PSI drift, forecasting with rolling backtest and honest baselines, file-based model registry |
| 4 | `modules/rag` | `python -m modules.rag.evaluate`, `uvicorn modules.rag.api:app` | Policy corpus, section-aware chunks, BM25 + dense fusion, role-filtered indexes (access control before ranking), extractive cited answers with abstention, corpus integrity manifest |
| 5 | `modules/agent` | `python -m modules.agent.cli INV-000004` | Bounded state machine: typed allow-listed tools, deterministic recommendation, human approval gate, verified narratives |
| 6 | `modules/erp` | `uvicorn modules.erp.app:app_from_env --factory` | Mock ERP with a safe OData subset, idempotent writes, ETag concurrency, and a client with retry/backoff/pagination; `ErpPort` is the interface a real connector must meet |
| 7 | `modules/qa` | `python -m modules.qa.release` | Release gate over all workflows (16 thresholds + regression vs baseline), adversarial suites, risk register whose controls must point at real tests |
| 8 | `modules/bi` | `python -m modules.bi.build` | Single-file HTML dashboard (no CDN) from the audited KPI queries, with an investigation drill-down and agent outcome metrics |

## Measured on this build (synthetic data, Linux sandbox)
* 215 tests pass across all workflows; the release gate verdict is PASS.
* WF3: on a time-split test set the best model (class-weighted logistic regression) reaches PR-AUC 0.92 at 3.3% prevalence, recall 0.94 in a 5% review queue; the boosted model and isolation forest scored lower. Features are close cousins of the SQL rules and labels come from the same injector, so this shows the pipeline works, not that it will beat rules on real data. Best forecaster backtested at ~15% sMAPE (seasonal-naive won).
* WF4: on 26 golden questions hybrid retrieval gives recall@5 = 1.00, MRR = 0.90. **The heuristic reranker did not help (MRR 0.86), so it is not used for answers.** Cited-answer correctness is 73%: paraphrased questions are the weak spot and the system abstains or cites a neighbouring section. Zero access leaks, 4/4 unanswerable questions refused. The set is small and the abstention threshold was set while looking at it.
* WF5/7: 60 hostile-text injection cases changed no decision and caused no write; 6/6 hijacked-model outputs were blocked (the first version of the verifier let one through, so it was tightened); 3/3 corpus-poisoning attacks detected.

## Where this build differs from the blueprint (and why)
* **No MLflow, XGBoost, sentence-transformers, LangGraph, OpenTelemetry, Ragas or Plotly** were installed in the build environment. Substitutes: a file-based registry, scikit-learn models, LSA as the dense encoder (a neural encoder is wired in and used automatically if installed, but was not tested), a plain state machine, JSON-line spans, a custom evaluation harness, and hand-written SVG charts. All are behind small interfaces.
* The **Claude API adapter** (`modules/agent/llm.py`) is written to the documented interface but was never run (no key, no network). The default narrative is a deterministic template.
* The **ERP is a mock**; no real SAP connection was tested. The ML anomaly scores are produced in batch from CSV history, standing in for a feature store.
* The dashboard is a static page, not the React/Next.js app in the blueprint.
* Nothing was run on macOS arm64.
