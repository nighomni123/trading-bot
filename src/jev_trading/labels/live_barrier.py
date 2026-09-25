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


# ---------------------------------------------------------------- fast path
#
# The polars implementation above is the reference: it is what the parity test
# pins against the live loop. It is O(horizon) list-concats per barrier, which
# costs minutes at horizon 240 on a dual-core laptop. The numpy path computes
# the identical first-touch outcome with strided windows and argmax, chunked to
# bound peak memory. `tests/test_live_barrier_parity.py` asserts the two agree.

import numpy as np  # noqa: E402


def build_live_barrier_labels_numpy(
    bars: pl.DataFrame,
    target_fraction: pl.Series,
    stop_fraction: pl.Series,
    side: int,
    horizon: int = 15,
    chunk: int = 50_000,
) -> pl.DataFrame:
    """Numpy equivalent of `build_live_barrier_labels`, same tie-break and validity.

    Chunked over rows so peak memory stays bounded on small machines.
    """
    if side not in (LONG, SHORT):
        raise ValueError("side must be LONG or SHORT")
    if horizon < 1:
        raise ValueError("horizon must be positive")

    n = bars.height
    decision_ts = bars["timestamp"].to_numpy()
    entry_ts = np.empty(n, dtype=decision_ts.dtype)
    entry_ts[:-1] = decision_ts[1:]
    entry_ts[-1] = 0

    open_ = bars["open"].to_numpy().astype(np.float64)
    high = bars["high"].to_numpy().astype(np.float64)
    low = bars["low"].to_numpy().astype(np.float64)
    entry = np.empty(n, dtype=np.float64)
    entry[:-1] = open_[1:]
    entry[-1] = 0.0

    tf = np.asarray(target_fraction, dtype=np.float64)
    sf = np.asarray(stop_fraction, dtype=np.float64)
    target = entry * (1.0 + side * tf)
    stop = entry * (1.0 - side * sf)

    outcome = np.full(n, -1, dtype=np.int8)
    tp_time = np.full(n, -1, dtype=np.int16)
    sl_time = np.full(n, -1, dtype=np.int16)

    window = np.lib.stride_tricks.sliding_window_view
    # A decision at row i needs entry at i+1 and a timeout exit bar at
    # i+horizon+1, so the last fully-valid decision row is n-horizon-1 (exclusive
    # bound n-horizon-1). Match the reference's `valid` mask exactly.
    limit = n - horizon - 1
    for start in range(0, max(limit, 0), chunk):
        end = min(start + chunk, limit)
        # Barriers are checked on bars i+1..i+horizon, so each row's window must
        # begin at the entry bar (i+1), not at i.
        base = start + 1
        if side == LONG:
            tp = window(high[base:end + horizon], horizon)[:end - start] >= target[start:end, None]
            sl = window(low[base:end + horizon], horizon)[:end - start] <= stop[start:end, None]
        else:
            tp = window(low[base:end + horizon], horizon)[:end - start] <= target[start:end, None]
            sl = window(high[base:end + horizon], horizon)[:end - start] >= stop[start:end, None]
        tp_first = np.where(tp.any(1), np.argmax(tp, axis=1) + 1, horizon + 1)
        sl_first = np.where(sl.any(1), np.argmax(sl, axis=1) + 1, horizon + 1)
        first = np.minimum(tp_first, sl_first)
        with np.errstate(invalid="ignore", divide="ignore"):
            # Mirror the reference: a barrier that was never touched is null (-1).
            tp_time[start:end] = np.where(tp_first > horizon, -1, tp_first).astype(np.int16)
            sl_time[start:end] = np.where(sl_first > horizon, -1, sl_first).astype(np.int16)
        # stop-favouring tie-break, identical to the polars reference
        code = np.where(first > horizon, TIMEOUT, np.where(sl_first <= tp_first, STOP_FIRST, TARGET_FIRST))
        outcome[start:end] = code.astype(np.int8)

    # Rows lacking a full forward window stay null, as in the reference.
    outcome[max(limit, 0):] = -1
    tp_time[max(limit, 0):] = -1
    sl_time[max(limit, 0):] = -1
    return pl.DataFrame({
        "decision_timestamp": decision_ts,
        "entry_timestamp": entry_ts,
        "entry_price": entry,
        "target_price": target,
        "stop_price": stop,
        "outcome": pl.Series(outcome).cast(pl.Int8),
        "time_to_tp": pl.Series(tp_time).cast(pl.Int16),
        "time_to_sl": pl.Series(sl_time).cast(pl.Int16),
    })
