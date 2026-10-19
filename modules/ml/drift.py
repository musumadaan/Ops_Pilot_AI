"""Population Stability Index drift check between a reference window and a current window."""
from __future__ import annotations

import numpy as np
import pandas as pd

MINOR, MAJOR = 0.10, 0.25


def psi(reference: pd.Series, current: pd.Series, bins: int = 10) -> float:
    ref = reference.dropna().to_numpy(float)
    cur = current.dropna().to_numpy(float)
    if len(ref) == 0 or len(cur) == 0:
        return float("nan")
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:                                   # (near-)constant or binary feature
        edges = np.unique(np.concatenate([[ref.min() - 0.5], np.unique(ref) + 0.5]))
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(ref, edges)[0] / len(ref)
    a = np.histogram(cur, edges)[0] / len(cur)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return float(np.sum((a - e) * np.log(a / e)))


def drift_report(reference: pd.DataFrame, current: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    rows = []
    for f in features:
        v = psi(reference[f], current[f])
        rows.append({"feature": f, "psi": round(v, 4),
                     "status": "major" if v > MAJOR else "minor" if v > MINOR else "stable"})
    return pd.DataFrame(rows).sort_values("psi", ascending=False).reset_index(drop=True)
