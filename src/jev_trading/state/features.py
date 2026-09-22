"""Point-in-time market-state features from 1m kline bars (Phase 2).

Every expression is backward-looking (shift / rolling / ewm): features on row i
use only bars 0..i, never future data. Warmup rows carry nulls — drop policy:
downstream filters them (e.g. ``df.drop_nulls(subset=[...])`` or drops rows
before the longest window it cares about).
"""
from __future__ import annotations

import polars as pl

from jev_trading.contracts import BAR_COLUMNS

DAY = 1440  # 1m bars per 24h

RET_WINDOWS = {"ret_1m": 1, "ret_5m": 5, "ret_15m": 15, "ret_60m": 60}
RV_WINDOWS = {"realized_vol_5m": 5, "realized_vol_30m": 30}

FEATURE_COLUMNS: tuple[str, ...] = (
    "ret_1m",
    "ret_5m",
    "ret_15m",
    "ret_60m",
    "realized_vol_5m",
    "realized_vol_30m",
    "atr_14",
    "ema20",
    "ema50",
    "ema200",
    "trend_score",
    "funding",
    "funding_z",
    "oi_change_1d",
    "vol_regime",
    "volume_z",
)

# clean name -> feature-frame column; decision-relevant subset for mock-Jev/live/policy.
STATE_FIELDS: dict[str, str] = {
    "ts": "timestamp",
    "close": "close",
    "ret_1m": "ret_1m",
    "ret_5m": "ret_5m",
    "ret_15m": "ret_15m",
    "ret_60m": "ret_60m",
    "rv_5m": "realized_vol_5m",
    "rv_30m": "realized_vol_30m",
    "atr_14": "atr_14",
    "ema20": "ema20",
    "ema50": "ema50",
    "ema200": "ema200",
    "trend_score": "trend_score",
    "funding": "funding",
    "funding_z": "funding_z",
    "oi_change_1d": "oi_change_1d",
    "vol_regime": "vol_regime",
    "volume_z": "volume_z",
}


def _rolling_z(col: str, window: int) -> pl.Expr:
    c = pl.col(col)
    mu = c.rolling_mean(window, min_samples=window)
    sd = c.rolling_std(window, min_samples=window).clip(lower_bound=1e-12)
    return (c - mu) / sd


def build_features(bars: pl.DataFrame) -> pl.DataFrame:
    """Return ``bars`` with FEATURE_COLUMNS appended; input columns are kept.

    Warmup rows may be null (longest windows: 1440-bar z-scores / 1d changes);
    downstream drops or filters them. Raises ValueError if a BAR_COLUMNS input
    is missing. Point-in-time safe: row i only sees bars 0..i.
    """
    missing = [c for c in BAR_COLUMNS if c not in bars.columns]
    if missing:
        raise ValueError(f"missing bar columns: {missing}")

    close = pl.col("close")
    prev_close = close.shift(1)
    log_ret = (close / prev_close).log()
    tr = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - prev_close).abs(),
        (pl.col("low") - prev_close).abs(),
    )

    df = bars.with_columns(
        *[(close / close.shift(n) - 1).alias(name) for name, n in RET_WINDOWS.items()],
        *[log_ret.rolling_std(w, min_samples=w).alias(name) for name, w in RV_WINDOWS.items()],
        atr_14=tr.rolling_mean(14, min_samples=14),
        ema20=close.ewm_mean(span=20, adjust=False, min_samples=20),
        ema50=close.ewm_mean(span=50, adjust=False, min_samples=50),
        ema200=close.ewm_mean(span=200, adjust=False, min_samples=200),
        funding=pl.col("funding_rate"),
        funding_z=_rolling_z("funding_rate", DAY),
        oi_change_1d=pl.col("open_interest") / pl.col("open_interest").shift(DAY) - 1,
        volume_z=_rolling_z("volume", DAY),
    )
    # ponytail: trend_score divides a multi-hour EMA gap by per-minute rv_30m, so it
    # often saturates at the ±1 clip; upgrade path: scale by rv * sqrt(gap_bars).
    return df.with_columns(
        trend_score=(
            (pl.col("ema20") / pl.col("ema200") - 1)
            / pl.col("realized_vol_30m").clip(lower_bound=1e-12)
        ).clip(-1.0, 1.0),
        vol_regime=(
            pl.col("realized_vol_30m")
            / pl.col("realized_vol_30m").rolling_mean(DAY, min_samples=DAY)
            - 1
        ),
    )


def build_state_dict(row: dict | pl.Series) -> dict:
    """JSON-serializable market state (numbers only) from one feature row.

    Accepts ``df.row(i, named=True)`` or a struct Series (latest row is used).
    Null/NaN warmup values are omitted; keys follow STATE_FIELDS clean names.
    """
    if isinstance(row, pl.Series):
        row = row.struct.unnest().row(-1, named=True)
    state: dict = {}
    for key, col in STATE_FIELDS.items():
        if col not in row:
            continue
        v = row[col]
        if v is None:
            continue
        v = int(v) if col == "timestamp" else float(v)
        if v == v:  # drop NaN
            state[key] = v
    return state
