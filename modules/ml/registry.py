"""Tiny file-based model registry (MLflow stand-in; the blueprint lists MLflow, which is not installed here).

    models/registry/<name>/<version>/{model.joblib, metadata.json}   +   models/registry/<name>/LATEST

Metadata records the data fingerprint, feature list, parameters and metrics so a score can be traced to the
exact training run. Versions are monotonically increasing integers.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib

DEFAULT_ROOT = Path("models/registry")


def fingerprint(df) -> str:
    import pandas as pd
    h = hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes())
    h.update(",".join(map(str, df.columns)).encode())
    return h.hexdigest()[:16]


def save(name: str, model: Any, metadata: dict[str, Any], root: Path = DEFAULT_ROOT) -> str:
    base = root / name
    base.mkdir(parents=True, exist_ok=True)
    versions = [int(p.name) for p in base.iterdir() if p.is_dir() and p.name.isdigit()]
    v = str(max(versions, default=0) + 1)
    d = base / v
    d.mkdir()
    joblib.dump(model, d / "model.joblib")
    meta = {**metadata, "name": name, "version": v, "saved_at": datetime.now(UTC).isoformat(timespec="seconds")}
    (d / "metadata.json").write_text(json.dumps(meta, indent=2, default=str))
    (base / "LATEST").write_text(v)
    return v


def load(name: str, version: str | None = None, root: Path = DEFAULT_ROOT) -> tuple[Any, dict[str, Any]]:
    base = root / name
    if not base.exists():
        raise FileNotFoundError(f"no model named '{name}' in {root}")
    version = version or (base / "LATEST").read_text().strip()
    d = base / version
    return joblib.load(d / "model.joblib"), json.loads((d / "metadata.json").read_text())
