"""Stage 12 deterministic market-state engine.

States are pre-declared (Phase 19). Thresholds use trailing/expanding statistics
only, never global percentiles computed with future data. States exist so a
later stage can reason about "HIGH_VOL + UP_TREND + OI_RISING" rather than 200
opaque numbers. They encode no direction or trade meaning.
"""
from __future__ import annotations

import numpy as np
import polars as pl

MARKET_STATE_VERSION = "market-state-v1"

#: Pre-declared state thresholds. z-score cut points are fixed constants; they
#: are NOT fitted to maximize any return.
STATE_THRESHOLDS = {
    "trend_z": 0.5,          # |ret_4h / realized_vol_24h| above this => directional
    "vol_low_z": -0.5, "vol_high_z": 1.0, "vol_extreme_z": 2.0,
    "range_expansion_z": 0.5,
    "oi_z": 0.5,
    "funding_z": 1.0,
    "location_high": 0.8, "location_low": 0.2,
}


def _z(expr: pl.Expr, window: int) -> pl.Expr:
    return (expr - expr.rolling_mean(window, min_samples=window // 4)) / (
        expr.rolling_std(window, min_samples=window // 4) + 1e-12
    )


def build_market_states(feats: pl.DataFrame) -> pl.DataFrame:
    """Attach deterministic state labels. Causal: all windows are trailing."""
    frame = feats
    t = STATE_THRESHOLDS
    exprs = []

    # TREND_STATE: normalized 4h momentum vs 24h vol.
    if "ret_4h" in frame.columns and "realized_vol_24h" in frame.columns:
        norm = pl.col("ret_4h") / (pl.col("realized_vol_24h") * np.sqrt(240) + 1e-12)
        exprs += [
            norm.alias("_trend_norm"),
            (norm > t["trend_z"]).cast(pl.Int8).alias("_trend_up"),
            (norm < -t["trend_z"]).cast(pl.Int8).alias("_trend_down"),
        ]
    # VOL_STATE from 24h realized vol z-score.
    if "realized_vol_24h" in frame.columns:
        vz = _z(pl.col("realized_vol_24h"), 1440)
        exprs += [
            vz.alias("_vol_z"),
            (vz < t["vol_low_z"]).cast(pl.Int8).alias("_vol_low"),
            ((vz >= t["vol_low_z"]) & (vz < t["vol_high_z"])).cast(pl.Int8).alias("_vol_normal"),
            ((vz >= t["vol_high_z"]) & (vz < t["vol_extreme_z"])).cast(pl.Int8).alias("_vol_high"),
            (vz >= t["vol_extreme_z"]).cast(pl.Int8).alias("_vol_extreme"),
        ]
    # RANGE_STATE from atr ratio (short/long).
    if "atr_frac_24h" in frame.columns and "atr_frac_72h" in frame.columns:
        rr = pl.col("atr_frac_24h") / (pl.col("atr_frac_72h") + 1e-12)
        exprs += [
            rr.alias("_range_ratio"),
            (rr < 1 - t["range_expansion_z"] / 2).cast(pl.Int8).alias("_range_compressed"),
            ((rr >= 1 - t["range_expansion_z"] / 2) & (rr < 1 + t["range_expansion_z"])).cast(pl.Int8).alias("_range_normal"),
            (rr >= 1 + t["range_expansion_z"]).cast(pl.Int8).alias("_range_expanding"),
        ]
    # OI_STATE
    if "oi_change_pct_1h_true" in frame.columns:
        oz = _z(pl.col("oi_change_pct_1h_true"), 1440)
        exprs += [
            (oz < -t["oi_z"]).cast(pl.Int8).alias("_oi_falling"),
            (oz.abs() <= t["oi_z"]).cast(pl.Int8).alias("_oi_neutral"),
            (oz > t["oi_z"]).cast(pl.Int8).alias("_oi_rising"),
        ]
    # FUNDING_STATE
    if "funding_zscore" in frame.columns:
        exprs += [
            (pl.col("funding_zscore") < -t["funding_z"]).cast(pl.Int8).alias("_funding_low"),
            (pl.col("funding_zscore").abs() <= t["funding_z"]).cast(pl.Int8).alias("_funding_normal"),
            (pl.col("funding_zscore") > t["funding_z"]).cast(pl.Int8).alias("_funding_high"),
        ]
    # PRICE_LOCATION_STATE from Donchian position over 24h.
    if "donchian_pos_24h" in frame.columns:
        exprs += [
            (pl.col("donchian_pos_24h") > t["location_high"]).cast(pl.Int8).alias("_loc_high"),
            (pl.col("donchian_pos_24h") < t["location_low"]).cast(pl.Int8).alias("_loc_low"),
            ((pl.col("donchian_pos_24h") >= t["location_low"]) & (pl.col("donchian_pos_24h") <= t["location_high"])).cast(pl.Int8).alias("_loc_mid"),
        ]

    if exprs:
        frame = frame.with_columns(exprs)

    # Materialize categorical states from the deterministic flags.
    states = {}
    if "_trend_up" in frame.columns:
        states["TREND_STATE"] = (
            pl.when(pl.col("_trend_down") == 1).then(pl.lit("DOWN"))
            .when(pl.col("_trend_up") == 1).then(pl.lit("UP"))
            .otherwise(pl.lit("NEUTRAL"))
        )
    if "_vol_low" in frame.columns:
        states["VOL_STATE"] = (
            pl.when(pl.col("_vol_extreme") == 1).then(pl.lit("EXTREME"))
            .when(pl.col("_vol_high") == 1).then(pl.lit("HIGH"))
            .when(pl.col("_vol_low") == 1).then(pl.lit("LOW"))
            .otherwise(pl.lit("NORMAL"))
        )
    if "_range_compressed" in frame.columns:
        states["RANGE_STATE"] = (
            pl.when(pl.col("_range_compressed") == 1).then(pl.lit("COMPRESSED"))
            .when(pl.col("_range_expanding") == 1).then(pl.lit("EXPANDING"))
            .otherwise(pl.lit("NORMAL"))
        )
    if "_oi_falling" in frame.columns:
        states["OI_STATE"] = (
            pl.when(pl.col("_oi_falling") == 1).then(pl.lit("FALLING"))
            .when(pl.col("_oi_rising") == 1).then(pl.lit("RISING"))
            .otherwise(pl.lit("NEUTRAL"))
        )
    if "_funding_low" in frame.columns:
        states["FUNDING_STATE"] = (
            pl.when(pl.col("_funding_low") == 1).then(pl.lit("LOW"))
            .when(pl.col("_funding_high") == 1).then(pl.lit("HIGH"))
            .otherwise(pl.lit("NORMAL"))
        )
    if "_loc_low" in frame.columns:
        states["PRICE_LOCATION_STATE"] = (
            pl.when(pl.col("_loc_high") == 1).then(pl.lit("HIGH"))
            .when(pl.col("_loc_low") == 1).then(pl.lit("LOW"))
            .otherwise(pl.lit("MID"))
        )

    if states:
        frame = frame.with_columns([expr.alias(name) for name, expr in states.items()])
    return frame


STATE_COLUMNS = ("TREND_STATE", "VOL_STATE", "RANGE_STATE", "OI_STATE", "FUNDING_STATE", "PRICE_LOCATION_STATE")


def state_manifest() -> dict:
    return {
        "state_version": MARKET_STATE_VERSION,
        "thresholds": STATE_THRESHOLDS,
        "states": list(STATE_COLUMNS),
        "note": "Pre-declared constants; trailing windows only; no PnL fitting.",
    }
