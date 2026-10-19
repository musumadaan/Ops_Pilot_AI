"""Create a self-contained folder (and zip) for one workflow, including only the workflows it depends on.

    python scripts/package_workflow.py wf4 --out dist          # one workflow
    python scripts/package_workflow.py all  --out dist         # every workflow + the full monorepo

Each folder is verified by running its own tests from a clean copy (see --verify).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMON = ["modules/__init__.py", "modules/common", "pyproject.toml", ".gitignore", ".env.example"]
OWN = {
    "wf1": ["modules/ocr", "modules/evaluation", "tests/unit", "tests/integration", "scripts/generate_invoices.py", "scripts/degrade_invoices.py",
            "datasets/synthetic/suppliers.csv", "datasets/synthetic/invoices/pdf", "datasets/synthetic/invoices/ground_truth"],
    "wf2": ["modules/analytics", "modules/data_quality", "dbt", "alembic.ini", "Makefile", ".sqlfluff", "tests/analytics", "tests/__init__.py",
            "scripts/generate_master_data.py", "scripts/load_synthetic.py", "scripts/explain_demo.py", "scripts/reset_dev_db.py",
            "datasets/synthetic/suppliers.csv"],
    "wf3": ["modules/ml", "tests/ml"],
    "wf4": ["modules/rag", "tests/rag", "datasets/synthetic/policies"],
    "wf5": ["modules/agent", "tests/agent"],
    "wf6": ["modules/erp", "tests/erp"],
    "wf7": ["modules/qa", "tests/qa"],
    "wf8": ["modules/bi", "tests/bi"],
}
DEPS = {"wf1": [], "wf2": [], "wf3": ["wf2"], "wf4": [], "wf6": ["wf2"], "wf5": ["wf2", "wf3", "wf4", "wf6"],
        "wf7": ["wf2", "wf3", "wf4", "wf5", "wf6"], "wf8": ["wf2", "wf5", "wf3", "wf4", "wf6"]}
NAMES = {"wf1": "document-intelligence", "wf2": "sql-data-engineering", "wf3": "ml-forecasting", "wf4": "rag-retrieval",
         "wf5": "agent-workflow", "wf6": "erp-integration", "wf7": "responsible-ai-qa", "wf8": "bi-dashboard"}
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "degraded", "target", "logs", "profiles.yml", "reports", "models")


def collect(wf: str) -> list[str]:
    paths = list(COMMON)
    for w in [*DEPS[wf], wf]:
        paths += OWN[w]
    if "wf2" in DEPS[wf] and "tests/analytics" not in paths:       # shared DB fixtures
        paths += ["tests/analytics/__init__.py", "tests/analytics/conftest.py", "tests/__init__.py"]
    return list(dict.fromkeys(paths))


def build(wf: str, out: Path) -> Path:
    dest = out / f"opspilot-{wf}-{NAMES[wf]}"
    if dest.exists():
        shutil.rmtree(dest)
    for rel in collect(wf):
        src = ROOT / rel
        if not src.exists():
            raise FileNotFoundError(rel)
        tgt = dest / rel
        tgt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, tgt, ignore=IGNORE, dirs_exist_ok=True) if src.is_dir() else shutil.copy2(src, tgt)
    (dest / "tests").mkdir(exist_ok=True)
    (dest / "tests/__init__.py").touch()
    readme = [f"# OpsPilot AI: {wf.upper()} {NAMES[wf].replace('-', ' ')}", "",
              f"Self-contained build of workflow {wf[2:]}. Includes dependencies: {', '.join(DEPS[wf]) or 'none'}.", "",
              "```bash", "pip install -e '.[dev]'", "python -m pytest -q", "```", ""]
    if wf == "wf2" or "wf2" in DEPS[wf]:
        readme += ["Database tests need PostgreSQL 16 (UTF8): set `OPSPILOT_TEST_DATABASE_URL`, e.g.",
                   "`postgresql+psycopg://user:pw@127.0.0.1:5432/opspilot_test` (the name must end in `_test`). Without it they are skipped.", ""]
    (dest / "README.md").write_text("\n".join(readme))
    return dest


def verify(folder: Path) -> tuple[bool, str]:
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], cwd=folder, capture_output=True, text=True, check=False)
    return p.returncode == 0, (p.stdout.strip().splitlines() or [""])[-1]


def zip_dir(folder: Path) -> Path:
    return Path(shutil.make_archive(str(folder), "zip", folder.parent, folder.name))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=[*OWN, "all"])
    ap.add_argument("--out", default="dist")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for wf in (OWN if a.which == "all" else [a.which]):
        d = build(wf, out)
        status = ""
        if a.verify:
            ok, line = verify(d)
            status = f"  [{'OK' if ok else 'FAIL'}] {line}"
        z = zip_dir(d)
        print(f"{wf}: {d.name} ({z.stat().st_size // 1024} KB){status}")


if __name__ == "__main__":
    main()
