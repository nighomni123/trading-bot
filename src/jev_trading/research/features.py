"""Stage 12 structural feature engine (families A-H).

Every feature is causal: a trailing window ending at or before t. Volatility
states use expanding/rolling statistics only (never a global percentile computed
with future data). Interactions are a small, pre-registered set, not a
combinatorial explosion.
"""
from __future__ import annotations

import numpy as np
import polars as pl

LONG_HORIZON_FEATURE_SET_VERSION = "long-horizon-features-v1"

MINUTE = 1
#: Bars for each named window (1-minute base).
WINDOWS = {"1h": 60, "2h": 120, "4h": 240, "8h": 480, "12h": 720, "24h": 1440, "48h": 2880, "72h": 4320}
VOL_WINDOWS = {"1h": 60, "4h": 240, "12h": 720, "24h": 1440, "72h": 4320}
RANGE_WINDOWS = {"4h": 240, "12h": 720, "24h": 1440, "72h": 4320}

#: Pre-registered, small interaction set (Phase 16 / G).
INTERACTIONS = (
    ("ret_4h", "realized_vol_24h", "trend_x_vol"),
    ("ret_4h", "oi_change_pct_1h", "trend_x_oi"),
    ("ret_4h", "funding_zscore", "trend_x_funding"),
    ("realized_vol_24h", "oi_change_pct_1h", "vol_x_oi"),
    ("funding_zscore", "oi_change_pct_1h", "funding_x_oi"),
    ("price_position_24h", "realized_vol_24h", "location_x_vol"),
)


def _logret() -> pl.Expr:
    return pl.col("close").log().diff()


def build_structural_features(bars: pl.DataFrame) -> pl.DataFrame:
    """Families A-H on the 1m bar spine. Causal throughout."""
    frame = bars.sort("timestamp")
    exprs = []

    # --- Family A: trend ---------------------------------------------------
    for name, w in WINDOWS.items():
        exprs.append(pl.col("close").pct_change(w).alias(f"ret_{name}"))
    ema12 = pl.col("close").ewm_mean(span=720, min_samples=60)
    ema48 = pl.col("close").ewm_mean(span=2880, min_samples=240)
    ema200 = pl.col("close").ewm_mean(span=10080, min_samples=720)
    exprs += [
        ((pl.col("close") - ema200) / ema200).alias("ema_dist_200"),
        ((ema12 - ema48) / ema48).alias("ema_dist_12_48"),
        ((ema48 - ema200) / ema200).alias("ema_dist_48_200"),
        (pl.col("close").pct_change(720) - pl.col("close").pct_change(60)).alias("momentum_spread_12h_1h"),
    ]

    # --- Family B: volatility state ---------------------------------------
    lr = _logret()
    vol = {}
    for name, w in VOL_WINDOWS.items():
        v = lr.rolling_std(window_size=w, min_samples=max(2, w // 4)).alias(f"realized_vol_{name}")
        vol[name] = v
        exprs.append(v)
    exprs += [
        (vol["1h"] / (vol["24h"] + 1e-12)).alias("short_long_vol_ratio"),
        (vol["24h"] - vol["72h"]) .alias("vol_acceleration_24_72"),
    ]

    # --- Family C: range / compression / expansion -------------------------
    prev_close = pl.col("close").shift(1)
    tr = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - prev_close).abs(),
        (pl.col("low") - prev_close).abs(),
    )
    frame = frame.with_columns(tr.alias("true_range"))
    for name, w in RANGE_WINDOWS.items():
        atr = pl.col("true_range").rolling_mean(window_size=w, min_samples=max(2, w // 4))
        exprs.append((atr / pl.col("close")).alias(f"atr_frac_{name}"))
        hi = pl.col("high").rolling_max(window_size=w, min_samples=max(2, w // 4))
        lo = pl.col("low").rolling_min(window_size=w, min_samples=max(2, w // 4))
        exprs += [
            ((pl.col("close") - lo) / (hi - lo + 1e-12)).alias(f"donchian_pos_{name}"),
            ((pl.col("close") - hi) / (hi + 1e-12)).alias(f"dist_from_high_{name}"),
            ((pl.col("close") - lo) / (lo + 1e-12)).alias(f"dist_from_low_{name}"),
        ]

    # --- Family E: open interest (neutral terminology) ----------------------
    if "open_interest" in frame.columns:
        oi = pl.col("open_interest")
        exprs += [
            (oi - oi.shift(1)).alias("oi_change_1h"),
            ((oi - oi.shift(1)) / (oi.shift(1) + 1e-12)).alias("oi_change_pct_1h"),
            ((oi - oi.shift(60)) / (oi.shift(60) + 1e-12)).alias("oi_change_pct_1h_true"),
            ((oi - oi.shift(60)) / (oi.shift(60) + 1e-12)).alias("oi_momentum_1h"),
        ]
        oi_mean = oi.rolling_mean(1440, min_samples=360)
        oi_std = oi.rolling_std(1440, min_samples=360)
        exprs.append(((oi - oi_mean) / (oi_std + 1e-12)).alias("oi_zscore_24h"))
        # Neutral quadrants (no long/short build/liquidation labels).
        price_up = pl.col("close") > pl.col("close").shift(1)
        oi_up = oi > oi.shift(1)
        exprs += [
            (price_up & oi_up).cast(pl.Int8).alias("PRICE_UP_OI_UP"),
            (price_up & ~oi_up).cast(pl.Int8).alias("PRICE_UP_OI_DOWN"),
            (~price_up & oi_up).cast(pl.Int8).alias("PRICE_DOWN_OI_UP"),
            (~price_up & ~oi_up).cast(pl.Int8).alias("PRICE_DOWN_OI_DOWN"),
        ]

    # --- Family F: funding --------------------------------------------------
    if "funding_rate" in frame.columns:
        fr = pl.col("funding_rate")
        f_mean = fr.rolling_mean(2880, min_samples=720)
        f_std = fr.rolling_std(2880, min_samples=720)
        exprs += [
            fr.alias("funding_rate"),
            ((fr - f_mean) / (f_std + 1e-12)).alias("funding_zscore"),
            (fr - fr.shift(1)).alias("funding_change"),
            f_mean.alias("funding_mean_24h"),
            fr.rolling_sum(2880, min_samples=720).alias("funding_sum_24h"),
        ]

    # --- Family H: time / seasonality ---------------------------------------
    ts = pl.col("timestamp")
    hour = ((ts // 3_600_000) % 24).cast(pl.Float32)
    dow = (((ts // 86_400_000) + 4) % 7).cast(pl.Float32)  # 1970-01-01 was Thursday
    exprs += [
        (2 * np.pi * hour / 24).sin().alias("sin_hour"),
        (2 * np.pi * hour / 24).cos().alias("cos_hour"),
        (2 * np.pi * dow / 7).sin().alias("sin_dow"),
        (2 * np.pi * dow / 7).cos().alias("cos_dow"),
    ]

    frame = frame.with_columns([e for e in exprs if e is not None])

    # --- Family G: pre-registered interactions ------------------------------
    inter = []
    for a, b, name in INTERACTIONS:
        if a in frame.columns and b in frame.columns:
            inter.append((pl.col(a) * pl.col(b)).alias(name))
    if inter:
        frame = frame.with_columns(inter)

    return frame


def feature_manifest(frame: pl.DataFrame) -> dict:
    from .firewall import is_label_column
    from .data import file_hash
    return {
        "feature_version": LONG_HORIZON_FEATURE_SET_VERSION,
        "n_features": frame.width,
        "n_rows": frame.height,
        "interactions": [name for _, _, name in INTERACTIONS],
        "windows_minutes": WINDOWS,
        "vol_windows": VOL_WINDOWS,
        "asserted_no_labels": not any(is_label_column(c) for c in frame.columns),
    }
