"""Phase 3 label engine: point-in-time outcome labels for 1m bars (docs/label-spec.md).

Contract: the outcome for row i uses only bars with index > i (close[i] is the
known reference price). Raw future returns first, derived y_up_15/y_dn_15 second.
Every label column is null where its full forward window doesn't fit (unknown is
null, never 0). Label code reads close/high/low only — never features, never
quant output.

ponytail: pure polars shifts/rolling windows — no loops, no new deps.
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

HORIZON = 15  # spec horizon H (bars)
EXTRA_HORIZONS = (5, 30, 60)
EXCURSION_WINDOW = 30  # retained alongside the spec 15-bar excursion (mfe/mae)
THRESHOLDS_PROBE = (0.0010, 0.0015, 0.0025, 0.0050)  # 0.10/0.15/0.25/0.50%


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
    rev = pl.col(col).reverse()
    rolled = rev.rolling_max(window, min_samples=window) if how == "max" else rev.rolling_min(window, min_samples=window)
    return rolled.reverse().shift(-1)


def compute_labels(bars: pl.DataFrame, threshold: float = DEFAULT_THRESHOLD) -> pl.DataFrame:
    """Append label columns; input columns other than timestamp are dropped.

    Raw: future_return_{5m,15m,30m,60m}, future_max/min_return_15m (H=15),
    mfe_30/mae_30 (30-bar excursion, retained). Derived: y_up_15/y_dn_15
    (Int8 0/1, null at tail).
    """
    close = pl.col("close")
    fr15 = close.shift(-HORIZON) / close - 1  # shared: alias + flag conditions
    out = [pl.col("timestamp")]
    for h in sorted({HORIZON, *EXTRA_HORIZONS}):
        out.append(((close.shift(-h) / close - 1) if h != HORIZON else fr15).alias(f"future_return_{h}m"))
    out += [
        (_fwd_extremum("high", HORIZON, "max") / close - 1).alias("future_max_return_15m"),
        (_fwd_extremum("low", HORIZON, "min") / close - 1).alias("future_min_return_15m"),
        (_fwd_extremum("high", EXCURSION_WINDOW, "max") / close - 1).alias("mfe_30"),
        (_fwd_extremum("low", EXCURSION_WINDOW, "min") / close - 1).alias("mae_30"),
        (
            pl.when(fr15.is_null())
            .then(pl.lit(None, dtype=pl.Int8))
            .when(fr15 >= threshold)
            .then(pl.lit(1, dtype=pl.Int8))
            .otherwise(pl.lit(0, dtype=pl.Int8))
            .alias("y_up_15")
        ),
        (
            pl.when(fr15.is_null())
            .then(pl.lit(None, dtype=pl.Int8))
            .when(fr15 <= -threshold)
            .then(pl.lit(1, dtype=pl.Int8))
            .otherwise(pl.lit(0, dtype=pl.Int8))
            .alias("y_dn_15")
        ),
    ]
    return bars.select(out)
