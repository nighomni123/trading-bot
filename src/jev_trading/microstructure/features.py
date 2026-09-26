"""Deterministic, causal microstructure feature engine.

Every feature at bar ``t`` is a function of information available at or before
``t`` only. Forward targets live in ``targets.py`` and are never imported here,
which is what makes the leakage audit meaningful.

Order-book features are implemented but remain NULL on real history because the
Binance public archive has no BTCUSDT bookDepth/bookTicker (see the Phase 0
audit). They are exercised by synthetic fixtures and reported as unpopulated on
real data; they are never imputed.
"""
from __future__ import annotations

import polars as pl

from .schema import FEATURE_SET_VERSION

EPS = 1e-12
MINUTE_MS = 60_000

#: Windows (in minutes) for which flow features are computed.
FLOW_WINDOWS = (1, 5, 15, 30)
CVD_WINDOWS = (1, 5, 15)


def _safe_ratio(numerator, denominator):
    return numerator / (denominator + EPS)


def flow_features(trade_bars: pl.DataFrame) -> pl.DataFrame:
    """Feature Group A from 1-minute aggregated trade bars.

    Input columns: timestamp, buy_volume, sell_volume, buy_notional, sell_notional,
    trade_count, sum_trade_size. All are already within-bar aggregates, so they
    are causal by construction.
    """
    frame = trade_bars
    total_volume = pl.col("buy_volume") + pl.col("sell_volume")
    total_notional = pl.col("buy_notional") + pl.col("sell_notional")
    return frame.with_columns(
        (pl.col("buy_volume") - pl.col("sell_volume")).alias("net_aggressive_volume"),
        _safe_ratio(pl.col("buy_volume") - pl.col("sell_volume"), total_volume).alias("volume_imbalance"),
        _safe_ratio(pl.col("buy_notional") - pl.col("sell_notional"), total_notional).alias("notional_imbalance"),
        _safe_ratio(2 * pl.col("buy_volume"), total_volume).alias("trade_imbalance"),
        _safe_ratio(pl.col("sum_trade_size"), pl.col("trade_count")).alias("average_trade_size"),
        (pl.col("buy_volume") + pl.col("sell_volume")).alias("total_volume"),
        total_notional.alias("total_notional"),
        pl.col("median_trade_size").alias("median_trade_size"),
        _safe_ratio(pl.col("large_trade_notional"), total_notional).alias("large_trade_fraction"),
    )


def rolling_flow_features(trade_bars: pl.DataFrame, windows=FLOW_WINDOWS) -> pl.DataFrame:
    """Trailing (causal) aggregates of flow over multiple windows."""
    exprs = []
    for w in windows:
        win = f"{w}m"
        buy = pl.col("buy_volume").rolling_sum(window_size=w, min_samples=1).alias(f"buy_volume_{win}")
        sell = pl.col("sell_volume").rolling_sum(window_size=w, min_samples=1).alias(f"sell_volume_{win}")
        buy_n = pl.col("buy_notional").rolling_sum(window_size=w, min_samples=1).alias(f"buy_notional_{win}")
        sell_n = pl.col("sell_notional").rolling_sum(window_size=w, min_samples=1).alias(f"sell_notional_{win}")
        total = pl.col("total_volume").rolling_sum(window_size=w, min_samples=1)
        exprs += [
            buy, sell, buy_n, sell_n,
            _safe_ratio(buy - sell, total).alias(f"volume_imbalance_{win}"),
            _safe_ratio(buy_n - sell_n, pl.col("total_notional").rolling_sum(window_size=w, min_samples=1)).alias(f"notional_imbalance_{win}"),
            _safe_ratio(pl.col("large_trade_notional").rolling_sum(window_size=w, min_samples=1), pl.col("total_notional").rolling_sum(window_size=w, min_samples=1)).alias(f"large_trade_fraction_{win}"),
        ]
    return trade_bars.with_columns(exprs)


def cvd_features(trade_bars: pl.DataFrame) -> pl.DataFrame:
    """Feature Group B: cumulative volume delta and price/CVD divergence.

    CVD at bar t is the running sum of net aggressive volume up to and including
    t (causal). Changes are trailing differences over CVD_WINDOWS. Computes the
    net column internally so it is safe to call before/after flow_features.
    """
    net = pl.col("buy_volume") - pl.col("sell_volume")
    frame = trade_bars.with_columns(net.alias("_net")).with_columns(
        pl.col("_net").cum_sum().alias("cvd")
    )
    exprs = []
    for w in CVD_WINDOWS:
        exprs.append((pl.col("cvd") - pl.col("cvd").shift(w)).alias(f"cvd_change_{w}m"))
    return frame.with_columns(exprs).with_columns(
        pl.col("_net").alias("net_aggressive_volume")
    ).drop("_net")


def price_cvd_divergence(frame: pl.DataFrame) -> pl.DataFrame:
    """Price/CVD divergence flags. Requires `close` (the bar spine), so it runs
    after the flow join in build_features, not inside cvd_features."""
    price_change = pl.col("close").pct_change()
    return frame.with_columns(
        ((price_change > 0) & (pl.col("net_aggressive_volume") < 0)).cast(pl.Int8).alias("price_up_cvd_down"),
        ((price_change < 0) & (pl.col("net_aggressive_volume") > 0)).cast(pl.Int8).alias("price_down_cvd_up"),
    )


def book_features(frame: pl.DataFrame) -> pl.DataFrame:
    """Feature Group C. Returns the frame unchanged (columns preserved) if book
    data is absent; callers must check presence rather than assume."""
    if "bid_price" not in frame.columns or frame["bid_price"].null_count() == frame.height:
        return frame
    mid = (pl.col("bid_price") + pl.col("ask_price")) / 2
    return frame.with_columns(
        (pl.col("ask_price") - pl.col("bid_price")).alias("spread"),
        mid.alias("mid_price"),
        (_safe_ratio(pl.col("ask_price") - pl.col("bid_price"), mid) * 10_000).alias("bid_ask_spread_bps"),
        _safe_ratio(pl.col("bid_qty") - pl.col("ask_qty"), pl.col("bid_qty") + pl.col("ask_qty")).alias("top_level_imbalance"),
        (pl.col("ask_price") * pl.col("bid_qty") + pl.col("bid_price") * pl.col("ask_qty"))
        .truediv(pl.col("bid_qty") + pl.col("ask_qty") + EPS).alias("microprice"),
    ).with_columns(
        (_safe_ratio(pl.col("microprice") - pl.col("mid_price"), pl.col("mid_price")) * 10_000).alias("microprice_mid_deviation_bps"),
    )


def oi_funding_features(bars: pl.DataFrame) -> pl.DataFrame:
    """Phase 3: open interest and funding state, plus price/OI quadrants."""
    frame = bars.with_columns(
        (pl.col("open_interest") - pl.col("open_interest").shift(1)).alias("oi_change_1m"),
        (pl.col("funding_rate") - pl.col("funding_rate").shift(1)).alias("funding_change"),
    )
    for w in (5, 15):
        frame = frame.with_columns(
            (pl.col("open_interest") - pl.col("open_interest").shift(w)).alias(f"oi_change_{w}m"),
            (pl.col("funding_rate") - pl.col("funding_rate").shift(w)).alias(f"funding_change_{w}m"),
        )
    oi_ma = pl.col("open_interest").rolling_mean(window_size=60, min_samples=20)
    frame = frame.with_columns(
        _safe_ratio(pl.col("open_interest") - pl.col("open_interest").shift(1), pl.col("open_interest").shift(1)).alias("oi_change_pct"),
        pl.col("funding_rate").alias("funding_level"),
        _safe_ratio(pl.col("funding_rate") - pl.col("funding_rate").rolling_mean(window_size=288, min_samples=60),
                    pl.col("funding_rate").rolling_std(window_size=288, min_samples=60)).alias("funding_zscore"),
    )
    price_up = pl.col("close") > pl.col("close").shift(1)
    oi_up = pl.col("open_interest") > pl.col("open_interest").shift(1)
    frame = frame.with_columns(
        (price_up & oi_up).cast(pl.Int8).alias("price_up_oi_up"),
        (price_up & ~oi_up).cast(pl.Int8).alias("price_up_oi_down"),
        (~price_up & oi_up).cast(pl.Int8).alias("price_down_oi_up"),
        (~price_up & ~oi_up).cast(pl.Int8).alias("price_down_oi_down"),
    )
    return frame.drop_nulls(subset=["oi_change_pct", "funding_zscore"])


def build_features(bars: pl.DataFrame, trade_bars: pl.DataFrame) -> pl.DataFrame:
    """Assemble the full feature frame on the kline grid.

    ``bars`` provides the timestamp spine plus OI/funding/price. ``trade_bars``
    provides flow aggregates. Trade bars are joined onto the spine so minutes
    without trades carry forward as zero-flow rather than disappearing.
    """
    spine = bars.sort("timestamp")
    flow = flow_features(trade_bars.sort("timestamp"))
    flow = rolling_flow_features(flow)
    flow = cvd_features(flow)
    joined = spine.join(flow, on="timestamp", how="left")
    # Minutes with no trades are genuine zero-flow, not missing data.
    zero_cols = [c for c in flow.columns if c != "timestamp" and flow.schema[c].is_numeric()]
    joined = joined.with_columns([pl.col(c).fill_null(0.0) for c in zero_cols if c in joined.columns])
    joined = price_cvd_divergence(joined)
    joined = oi_funding_features(joined)
    return joined.sort("timestamp")
