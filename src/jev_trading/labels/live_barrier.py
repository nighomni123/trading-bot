"""Stage 9: barrier labels that match the live path estimator exactly.

The live economic gate consumes `live_intelligence/quant/path.py`. Any model
trained on different label semantics produces an AUC that does not describe the
event the gate actually acts on. This module provides a vectorized labeler with
proven parity to `path.py`.
"""
from __future__ import annotations

import polars as pl

MINUTE_MS = 60_000

LONG = 1
SHORT = -1


def atr_fraction(bars: pl.DataFrame, length: int = 14) -> pl.Series:
    """Causal ATR as a fraction of close, available at the decision bar."""
    previous = pl.col("close").shift(1)
    true_range = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - previous).abs(),
        (pl.col("low") - previous).abs(),
    )
    frame = bars.select(
        true_range.alias("tr"),
        pl.col("close"),
    ).with_columns(
        pl.col("tr").rolling_mean(window_size=length, min_samples=length).alias("atr")
    )
    return (frame["atr"] / frame["close"]).fill_null(0.0)


def build_live_barrier_labels(
    bars: pl.DataFrame,
    target_fraction: pl.Series,
    stop_fraction: pl.Series,
    side: int,
    horizon: int = 15,
) -> pl.DataFrame:
    """Vectorized equivalent of `quant/path.build_completed_path_samples`.

    Entry is the next bar's open; barriers are checked on bars i+1..i+horizon;
    a timeout exits at open[i+horizon+1]. Same-bar TP/SL contact resolves to
    STOP_FIRST, matching the live estimator's stop-favouring tie-break.
    """
    if side not in (LONG, SHORT):
        raise ValueError("side must be LONG or SHORT")
    if horizon < 1:
        raise ValueError("horizon must be positive")

    n = bars.height
    entry = pl.col("open").shift(-1)
    target = entry * (1.0 + side * target_fraction)
    stop = entry * (1.0 - side * stop_fraction)
    valid = pl.col("timestamp").shift(-(horizon + 1)).is_not_null()

    tp_terms, sl_terms = [], []
    for offset in range(1, horizon + 1):
        high = pl.col("high").shift(-offset)
        low = pl.col("low").shift(-offset)
        tp_terms.append(
            pl.when(high >= target if side == LONG else low <= target)
            .then(pl.lit(offset, dtype=pl.Int16)).otherwise(pl.lit(None, dtype=pl.Int16))
        )
        sl_terms.append(
            pl.when(low <= stop if side == LONG else high >= stop)
            .then(pl.lit(offset, dtype=pl.Int16)).otherwise(pl.lit(None, dtype=pl.Int16))
        )

    tp_time = pl.concat_list(tp_terms).list.min().cast(pl.Int16)
    sl_time = pl.concat_list(sl_terms).list.min().cast(pl.Int16)
    # Whichever barrier is touched first decides; a simultaneous touch resolves
    # to STOP_FIRST. A later stop must never override an earlier target.
    tp_time_safe = tp_time.fill_null(32_767)
    sl_time_safe = sl_time.fill_null(32_767)
    outcome = (
        pl.when(~valid).then(pl.lit(None, dtype=pl.Int8))
        .when(tp_time.is_null() & sl_time.is_null()).then(pl.lit(1, dtype=pl.Int8))
        .when(sl_time_safe <= tp_time_safe).then(pl.lit(0, dtype=pl.Int8))
        .otherwise(pl.lit(2, dtype=pl.Int8))
    )
    return bars.select(
        pl.col("timestamp").alias("decision_timestamp"),
        pl.col("timestamp").shift(-1).alias("entry_timestamp"),
        entry.alias("entry_price"),
        target.alias("target_price"),
        stop.alias("stop_price"),
        outcome.alias("outcome"),
        tp_time.alias("time_to_tp"),
        sl_time.alias("time_to_sl"),
    )


STOP_FIRST, TIMEOUT, TARGET_FIRST = 0, 1, 2
