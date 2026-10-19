from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from modules.analytics.synthetic import Generator
from modules.ml import registry
from modules.ml.anomaly import precision_recall_at_k, score, time_split, train
from modules.ml.drift import drift_report, psi
from modules.ml.features import FEATURES, build_features
from modules.ml.forecast import METHODS, backtest, forecast, smape

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    d = tmp_path_factory.mktemp("ml")
    Generator(ROOT / "datasets/synthetic/suppliers.csv", 7, n_po=1500, per_type=10, start=date(2024, 10, 1)).write(d)
    return d


@pytest.fixture(scope="module")
def df(data):
    return build_features(data)


def test_features_complete_and_finite(df):
    assert list(df[FEATURES].columns) == FEATURES and not df[FEATURES].isna().any().any()
    assert np.isfinite(df[FEATURES].to_numpy(float)).all() and 0.01 < df.label.mean() < 0.3


def test_history_features_do_not_see_the_future(data, df):
    """Dropping the latest invoices must not change any earlier row's features."""
    cut = df.sort_values("received_at").index[len(df) // 2]
    half = build_features_truncated(data, cut)
    common = half.index.intersection(df.index)
    pd.testing.assert_frame_equal(df.loc[common, FEATURES].astype(float), half.loc[common, FEATURES].astype(float),
                                  check_exact=False, atol=1e-9)


def build_features_truncated(data, last_invoice_id):
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    for f in data.glob("*.csv"):
        shutil.copy(f, tmp / f.name)
    inv = pd.read_csv(tmp / "invoice.csv")
    inv["_t"] = pd.to_datetime(inv["received_at"], utc=True)
    t_cut = inv.loc[inv.invoice_id == last_invoice_id, "_t"].iloc[0]
    inv[inv["_t"] <= t_cut].drop(columns="_t").to_csv(tmp / "invoice.csv", index=False)
    return build_features(tmp)


def test_time_split_is_chronological(df):
    tr, te = time_split(df)
    assert tr["received_at"].max() <= te["received_at"].min() and len(tr) > len(te)


def test_precision_recall_at_k_known_values():
    y = np.array([1, 0, 1, 0, 0, 0, 0, 0, 0, 0])
    s = np.array([.9, .8, .7, .1, .1, .1, .1, .1, .1, .1])
    p, r = precision_recall_at_k(y, s, frac=0.3)
    assert (p, r) == (pytest.approx(2 / 3), 1.0)


def test_training_beats_chance_registers_and_scores(df, tmp_path):
    res = train(df, root=tmp_path)
    prev = df.label.mean()
    best = res.metrics[res.chosen]
    assert best["pr_auc"] > 5 * prev and best["recall@5%"] > 0.5
    bundle, meta = registry.load("invoice_anomaly", root=tmp_path)
    assert meta["version"] == res.version == "1" and meta["features"] == FEATURES and meta["data_fingerprint"]
    sc = score(bundle, df.head(50))
    assert set(sc.columns) == {"score", "flag", "reasons"} and sc.score.between(0, 1).all()
    top = score(bundle, df[df.label == 1].head(20))
    assert top["reasons"].map(len).gt(0).mean() > 0.5                     # flagged items come with reasons


def test_registry_versions_increment_and_missing_raises(tmp_path):
    assert registry.save("m", {"a": 1}, {}, tmp_path) == "1" and registry.save("m", {"a": 2}, {}, tmp_path) == "2"
    assert registry.load("m", root=tmp_path)[0] == {"a": 2} and registry.load("m", "1", tmp_path)[0] == {"a": 1}
    with pytest.raises(FileNotFoundError):
        registry.load("nope", root=tmp_path)


def test_training_refuses_without_enough_positives(df, tmp_path):
    with pytest.raises(ValueError):
        train(df.assign(label=0), root=tmp_path)


def test_psi_properties():
    rng = np.random.default_rng(0)
    a = pd.Series(rng.normal(0, 1, 5000))
    assert psi(a, pd.Series(rng.normal(0, 1, 5000))) < 0.05
    assert psi(a, pd.Series(rng.normal(2, 1, 5000))) > 0.25
    rep = drift_report(pd.DataFrame({"x": a}), pd.DataFrame({"x": a + 3}), ["x"])
    assert rep.loc[0, "status"] == "major"
    assert psi(pd.Series([0, 1] * 50), pd.Series([0, 1] * 50)) < 0.01       # binary feature


def _seasonal(n=48, trend=0.0, noise=0.0, seed=0):
    idx = pd.date_range("2021-01-01", periods=n, freq="MS")
    base = 100 + 30 * np.sin(2 * np.pi * (idx.month - 1) / 12) + trend * np.arange(n)
    return pd.Series(base + np.random.default_rng(seed).normal(0, noise, n), index=idx)


def test_seasonal_naive_is_perfect_on_pure_seasonality():
    s = _seasonal()
    summary, _ = backtest(s)
    assert summary.loc["seasonal_naive", "smape"] < 1e-6


def test_ridge_wins_on_trend_plus_season():
    summary, _ = backtest(_seasonal(trend=3.0, noise=1.0))
    assert summary.index[0] == "ridge_trend_season"


def test_forecast_shape_and_ordered_intervals():
    fc = forecast(_seasonal(trend=2.0, noise=3.0), horizon=3)
    assert len(fc) == 3 and (fc.lower <= fc.forecast).all() and (fc.forecast <= fc.upper).all() and (fc.lower >= 0).all()
    assert fc.month.is_monotonic_increasing


def test_smape_bounds():
    assert smape(np.array([1., 2.]), np.array([1., 2.])) == 0 and smape(np.array([1.]), np.array([0.])) == pytest.approx(200)
    assert set(METHODS) == {"seasonal_naive", "moving_avg_3", "ridge_trend_season"}
