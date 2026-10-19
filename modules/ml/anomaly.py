"""Train, evaluate, register and apply the invoice-anomaly model.

Evaluation is a *time* split (train on the earliest 70%, test on the latest 30%) because random splits leak
supplier history. Reported: PR-AUC (the right headline metric at low prevalence), ROC-AUC, the Brier score of the
predicted probabilities (class-weighted logistic regression is *not* calibrated; the boosted model is wrapped in
isotonic calibration), and precision / recall at a fixed review capacity (top 5% of invoices), which is the
number an AP team actually cares about.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import registry
from .features import FEATURES

SEED = 42


def time_split(df: pd.DataFrame, train_frac: float = 0.7) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.sort_values("received_at")
    k = int(len(df) * train_frac)
    return df.iloc[:k], df.iloc[k:]


def precision_recall_at_k(y: np.ndarray, score: np.ndarray, frac: float = 0.05) -> tuple[float, float]:
    k = max(1, int(round(len(y) * frac)))
    top = np.argsort(-score, kind="stable")[:k]
    tp = int(y[top].sum())
    return tp / k, (tp / y.sum() if y.sum() else float("nan"))


def evaluate(y: np.ndarray, score: np.ndarray, proba: bool = True, review_frac: float = 0.05) -> dict[str, float]:
    p, r = precision_recall_at_k(y, score, review_frac)
    out = {"pr_auc": float(average_precision_score(y, score)), "roc_auc": float(roc_auc_score(y, score)),
           f"precision@{int(review_frac * 100)}%": p, f"recall@{int(review_frac * 100)}%": r,
           "prevalence": float(y.mean())}
    if proba:
        out["brier"] = float(brier_score_loss(y, score))
    return out


def make_models() -> dict[str, object]:
    return {
        "logreg": make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced", C=0.5)),
        "hgb": CalibratedClassifierCV(
            HistGradientBoostingClassifier(max_depth=4, learning_rate=0.08, max_iter=200, random_state=SEED,
                                           class_weight="balanced"), method="isotonic", cv=3),
    }


@dataclass
class TrainResult:
    version: str
    metrics: dict[str, dict[str, float]]
    chosen: str
    importances: pd.DataFrame


def train(df: pd.DataFrame, name: str = "invoice_anomaly", root=registry.DEFAULT_ROOT, save: bool = True) -> TrainResult:
    tr, te = time_split(df)
    if tr["label"].sum() < 10 or te["label"].sum() < 5:
        raise ValueError("too few positive labels in train/test split to train a reliable model")
    Xtr, ytr, Xte, yte = tr[FEATURES], tr["label"].to_numpy(), te[FEATURES], te["label"].to_numpy()
    metrics: dict[str, dict[str, float]] = {}
    fitted = {}
    for k, m in make_models().items():
        m.fit(Xtr, ytr)
        fitted[k] = m
        metrics[k] = evaluate(yte, m.predict_proba(Xte)[:, 1])
    iso = IsolationForest(n_estimators=200, random_state=SEED, contamination="auto").fit(Xtr)
    metrics["isolation_forest (unsupervised)"] = evaluate(yte, -iso.score_samples(Xte), proba=False)
    chosen = max(fitted, key=lambda k: metrics[k]["pr_auc"])
    model = fitted[chosen]
    imp = permutation_importance(model, Xte, yte, scoring="average_precision", n_repeats=5, random_state=SEED)
    importances = (pd.DataFrame({"feature": FEATURES, "importance": imp.importances_mean})
                   .sort_values("importance", ascending=False).reset_index(drop=True))
    ref = Xtr
    bundle = {"model": model, "features": FEATURES, "medians": ref.median().to_dict(),
              "review_threshold": float(np.quantile(model.predict_proba(Xtr)[:, 1], 0.95))}
    version = ""
    if save:
        version = registry.save(name, bundle, {
            "chosen": chosen, "metrics": metrics, "features": FEATURES, "data_fingerprint": registry.fingerprint(df[FEATURES]),
            "train_rows": len(tr), "test_rows": len(te), "split": "time 70/30", "seed": SEED,
            "permutation_importance": importances.head(8).to_dict("records")}, root)
    return TrainResult(version, metrics, chosen, importances)


def score(bundle: dict, df: pd.DataFrame, explain_top: int = 3) -> pd.DataFrame:
    """Score invoices; ``reasons`` lists the features whose replacement by the training median lowers the score most
    (occlusion attribution - a what-if explanation, not Shapley values)."""
    feats = bundle["features"]
    X = df[feats]
    p = bundle["model"].predict_proba(X)[:, 1]
    deltas = {}
    for f in feats:
        Xo = X.copy()
        Xo[f] = bundle["medians"][f]
        deltas[f] = p - bundle["model"].predict_proba(Xo)[:, 1]
    D = pd.DataFrame(deltas, index=df.index)
    reasons = D.apply(lambda r: [f"{c} (+{r[c]:.2f})" for c in r.sort_values(ascending=False).index[:explain_top] if r[c] > 0.01], axis=1)
    return pd.DataFrame({"score": p, "flag": p >= bundle["review_threshold"], "reasons": reasons}, index=df.index)
