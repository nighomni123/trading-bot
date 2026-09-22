import json

import numpy as np
import polars as pl

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.state.features import FEATURE_COLUMNS, STATE_FIELDS, build_features, build_state_dict


def make_bars(n: int, seed: int = 0, start_ts: int = 1_700_000_000_000) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, 0.0005, n)
    close = 50_000 * np.cumprod(1 + ret)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.0005, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.0005, n))
    return pl.DataFrame(
        {
            "timestamp": start_ts + np.arange(n, dtype=np.int64) * 60_000,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(1.0, 100.0, n),
            "funding_rate": 1e-4 + rng.normal(0, 1e-5, n),
            "open_interest": 1e9 * np.cumprod(1 + rng.normal(0, 1e-4, n)),
        }
    )


def test_no_lookahead():
    n = 400
    bars = make_bars(n, seed=1)
    feats = build_features(bars)
    for i in (60, 150, 299, 380):
        tail = make_bars(n - i - 1, seed=100 + i, start_ts=int(bars["timestamp"][i + 1]))
        mutated = pl.concat([bars.head(i + 1), tail])
        got = build_features(mutated)
        # mutated tail must actually change features after i (non-vacuous check)...
        assert not feats.tail(n - i - 1).equals(got.tail(n - i - 1))
        # ...yet features at rows <= i are byte-identical (no lookahead).
        assert feats.head(i + 1).equals(got.head(i + 1)), f"features at rows <= {i} changed"


def test_schema_and_bounds():
    feats = build_features(make_bars(3000, seed=2))
    for col in (*BAR_COLUMNS, *FEATURE_COLUMNS):
        assert col in feats.columns, f"missing column {col}"
    trend = feats["trend_score"].drop_nulls()
    assert len(trend) > 0
    assert trend.min() >= -1.0 and trend.max() <= 1.0


def test_warmup_ema_nulls():
    feats = build_features(make_bars(400, seed=3))
    ema200 = feats["ema200"]
    assert ema200.head(199).is_null().all()  # first ema200 window rows null
    assert ema200[199] is not None and not np.isnan(ema200[199])
    ema20 = feats["ema20"]
    assert ema20.head(19).is_null().all()
    assert ema20[19] is not None and not np.isnan(ema20[19])


def test_short_series_null_safe():
    feats = build_features(make_bars(50, seed=4))
    assert feats["oi_change_1d"].is_null().all()
    assert feats["funding_z"].is_null().all()
    assert feats["vol_regime"].is_null().all()
    assert not feats["ret_5m"].tail(10).is_null().any()


def test_zero_oi_yields_null_not_inf():
    bars = make_bars(2000, seed=9)
    oi = bars["open_interest"].to_list()
    oi[500] = 0.0  # zero divisor for row 500 + 1440
    feats = build_features(bars.with_columns(pl.Series("open_interest", oi)))
    assert feats["oi_change_1d"][1940] is None
    arr = feats["oi_change_1d"].drop_nulls().to_numpy()
    assert not np.isinf(arr).any() and not np.isnan(arr).any()


def test_build_state_dict():
    feats = build_features(make_bars(3000, seed=5))
    d = build_state_dict(feats.row(2500, named=True))
    assert d["ts"] == feats["timestamp"][2500]
    assert d["close"] == feats["close"][2500]
    assert d["ema200"] == feats["ema200"][2500]
    assert "trend_score" in d and "volume_z" in d
    assert "open" not in d and "high" not in d
    assert all(isinstance(v, (int, float)) for v in d.values())
    json.dumps(d)

    warm = build_state_dict(feats.row(0, named=True))
    assert "ema200" not in warm and "trend_score" not in warm
    json.dumps(warm)

    srs = feats.select(pl.struct(list(STATE_FIELDS.values()))).to_series().tail(1)
    assert build_state_dict(srs) == build_state_dict(feats.row(2999, named=True))
