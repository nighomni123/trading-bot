"""Point-in-time outcome labels for one-minute BTCUSDT perpetual bars.

Every outcome at row ``t`` uses only bars after ``t``. Missing forward windows
are null, never zero. The compact wide frame contains returns, excursions, and
binary direction labels; path-dependent TP/SL outcomes are available through
``compute_path_labels`` for an explicitly selected barrier pair.
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

HORIZON = 15
HORIZONS = (5, 15, 30, 60)
EXTRA_HORIZONS = (5, 30, 60)
EXCURSION_WINDOW = 30
THRESHOLDS_PROBE = (0.0010, 0.0015, 0.0025, 0.0050)
PATH_LEVELS = (0.0010, 0.0015, 0.0020, 0.0030)
PATH_GRID = tuple((tp, sl) for tp in PATH_LEVELS for sl in PATH_LEVELS)
DEFAULT_PATH_HORIZON = 60


def _default_threshold() -> float:
    """Round-trip cost hurdle from configs/costs.json (single source)."""
    p = Path(__file__).resolve().parents[3] / "configs" / "costs.json"
    try:
        c = json.loads(p.read_text())
        return 2 * (float(c["taker_fee_pct"]) + float(c["slippage_pct"])) / 100
    except (OSError, ValueError, KeyError):
        return 0.0014


DEFAULT_THRESHOLD = _default_threshold()


def _fwd_extremum(col: str, window: int, how: str) -> pl.Expr:
    """Rolling max/min over bars i+1..i+window; null unless the full window fits."""
    if how not in {"max", "min"}:
        raise ValueError("how must be 'max' or 'min'")
    rev = pl.col(col).reverse()
    rolled = rev.rolling_max(window, min_samples=window) if how == "max" else rev.rolling_min(window, min_samples=window)
    return rolled.reverse().shift(-1)


def _direction_labels(fr: pl.Expr, threshold: float, horizon: int) -> list[pl.Expr]:
    suffix = "15" if horizon == HORIZON else f"{horizon}m"
    return [
        pl.when(fr.is_null())
        .then(pl.lit(None, dtype=pl.Int8))
        .when(fr >= threshold)
        .then(pl.lit(1, dtype=pl.Int8))
        .otherwise(pl.lit(0, dtype=pl.Int8))
        .alias(f"y_up_{suffix}"),
        pl.when(fr.is_null())
        .then(pl.lit(None, dtype=pl.Int8))
        .when(fr <= -threshold)
        .then(pl.lit(1, dtype=pl.Int8))
        .otherwise(pl.lit(0, dtype=pl.Int8))
        .alias(f"y_dn_{suffix}"),
    ]


def compute_path_labels(
    bars: pl.DataFrame,
    tp_threshold: float,
    sl_threshold: float,
    horizon: int = DEFAULT_PATH_HORIZON,
) -> pl.DataFrame:
    """Return exact first-touch outcomes for one TP/SL pair.

    The window is strictly ``t+1..t+horizon``. A TP hit is ``high >= close[t] *
    (1 + tp_threshold)`` and an SL hit is ``low <= close[t] * (1 - sl_threshold)``.
    If both barriers touch in the same bar, the conservative result is SL-first
    (``tp_before_sl = 0``). If neither touches, the race is null and ``timeout=1``.
    """
    if horizon < 1:
        raise ValueError("horizon must be positive")
    if not 0 < tp_threshold < 1 or not 0 < sl_threshold < 1:
        raise ValueError("TP and SL thresholds must be fractions between zero and one")

    ref = pl.col("close")
    valid_parts = [pl.col("timestamp").shift(-offset).is_not_null() for offset in range(1, horizon + 1)]
    valid = pl.all_horizontal(valid_parts)
    tp_seen = pl.lit(False)
    sl_seen = pl.lit(False)
    tp_terms: list[pl.Expr] = []
    sl_terms: list[pl.Expr] = []
    tp_event_terms: list[pl.Expr] = []
    sl_event_terms: list[pl.Expr] = []

    for offset in range(1, horizon + 1):
        high = pl.col("high").shift(-offset)
        low = pl.col("low").shift(-offset)
        tp_hit = high >= ref * (1.0 + tp_threshold)
        sl_hit = low <= ref * (1.0 - sl_threshold)
        tp_first = tp_hit & ~sl_seen
        sl_first = sl_hit & ~tp_seen
        tp_terms.append(
            pl.when(tp_hit)
            .then(pl.lit(offset, dtype=pl.Int16))
            .otherwise(pl.lit(None, dtype=pl.Int16))
        )
        sl_terms.append(
            pl.when(sl_hit)
            .then(pl.lit(offset, dtype=pl.Int16))
            .otherwise(pl.lit(None, dtype=pl.Int16))
        )
        tp_event_terms.append(
            pl.when(tp_first)
            .then(pl.lit(offset, dtype=pl.Int16))
            .otherwise(pl.lit(None, dtype=pl.Int16))
        )
        sl_event_terms.append(
            pl.when(sl_first)
            .then(pl.lit(offset, dtype=pl.Int16))
            .otherwise(pl.lit(None, dtype=pl.Int16))
        )
        tp_seen = tp_seen | tp_hit
        sl_seen = sl_seen | sl_hit

    tp_time = pl.concat_list(tp_terms).list.min().cast(pl.Int16)
    sl_time = pl.concat_list(sl_terms).list.min().cast(pl.Int16)
    tp_event_time = pl.concat_list(tp_event_terms).list.min().cast(pl.Int16)
    sl_event_time = pl.concat_list(sl_event_terms).list.min().cast(pl.Int16)
    race = (
        pl.when(~valid)
        .then(pl.lit(None, dtype=pl.Int8))
        .when(tp_event_time.is_null() & sl_event_time.is_null())
        .then(pl.lit(None, dtype=pl.Int8))
        .when(tp_event_time.is_not_null() & sl_event_time.is_null())
        .then(pl.lit(1, dtype=pl.Int8))
        .when(tp_event_time.is_not_null() & sl_event_time.is_not_null() & (tp_event_time < sl_event_time))
        .then(pl.lit(1, dtype=pl.Int8))
        .otherwise(pl.lit(0, dtype=pl.Int8))
    )
    timeout = (
        pl.when(~valid)
        .then(pl.lit(None, dtype=pl.Int8))
        .when(tp_time.is_null() & sl_time.is_null())
        .then(pl.lit(1, dtype=pl.Int8))
        .otherwise(pl.lit(0, dtype=pl.Int8))
    )
    return bars.with_columns(
        valid.alias("_valid"),
        tp_time.alias("time_to_tp"),
        sl_time.alias("time_to_sl"),
        race.alias("tp_before_sl"),
        timeout.alias("timeout"),
    ).select("timestamp", "tp_before_sl", "time_to_tp", "time_to_sl", "timeout")


def compute_labels(
    bars: pl.DataFrame,
    threshold: float = DEFAULT_THRESHOLD,
    path_pairs: tuple[tuple[float, float], ...] | None = None,
) -> pl.DataFrame:
    """Append return, excursion, direction, and optional path outcome labels.

    ``path_pairs`` is intentionally explicit: the full 4x4 barrier grid can be
    large on millions of rows, so experiments select and lock the pairs they need.
    """
    close = pl.col("close")
    out: list[pl.Expr] = [pl.col("timestamp")]
    returns: dict[int, pl.Expr] = {}
    maxima: dict[int, pl.Expr] = {}
    minima: dict[int, pl.Expr] = {}
    for horizon in HORIZONS:
        fr = close.shift(-horizon) / close - 1.0
        returns[horizon] = fr
        maxima[horizon] = _fwd_extremum("high", horizon, "max") / close - 1.0
        minima[horizon] = _fwd_extremum("low", horizon, "min") / close - 1.0
        out.append(fr.alias(f"future_return_{horizon}m"))

    for horizon in HORIZONS:
        out.append(maxima[horizon].alias(f"future_max_return_{horizon}m"))
        out.append(minima[horizon].alias(f"future_min_return_{horizon}m"))

    # Explicit excursion names, with the historical 30m aliases preserved.
    for horizon in HORIZONS:
        out.append(maxima[horizon].alias(f"mfe_{horizon}m"))
        out.append(minima[horizon].alias(f"mae_{horizon}m"))
    out.extend([maxima[30].alias("mfe_30"), minima[30].alias("mae_30")])

    for horizon in HORIZONS:
        out.extend(_direction_labels(returns[horizon], threshold, horizon))

    result = bars.select(out)
    for tp, sl in path_pairs or ():
        pair = compute_path_labels(bars, tp, sl)
        suffix = f"{tp * 10000:.0f}bp_{sl * 10000:.0f}bp"
        pair = pair.rename({
            "tp_before_sl": f"tp_before_sl_{suffix}",
            "time_to_tp": f"time_to_tp_{suffix}",
            "time_to_sl": f"time_to_sl_{suffix}",
            "timeout": f"timeout_{suffix}",
        })
        result = result.join(pair, on="timestamp", how="left")
    return result
