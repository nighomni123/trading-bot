"""Deterministic event study: forward-return behavior conditional on events.

This is discovery, not selection. Every event is reported with its sample count,
unconditional baseline, conditional distribution, difference, and a standard
error. Nothing is ranked or promoted here.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from .schema import ALPHA_EXPERIMENT_VERSION, EVENT_DEFINITION_VERSION, TARGET_VERSION
from .targets import forward_distribution_stats

STUDY_HORIZONS = (5, 10, 20, 30, 60, 240)


def _welch_diff_mean(a: np.ndarray, b: np.ndarray) -> dict:
    """Mean difference and standard error, two-sample Welch."""
    if a.size < 2 or b.size < 2:
        return {"diff": None, "stderr": None, "t": None}
    diff = a.mean() - b.mean()
    se = np.sqrt(a.var(ddof=1) / a.size + b.var(ddof=1) / b.size)
    return {"diff": float(diff), "stderr": float(se), "t": float(diff / se) if se > 0 else None}


def event_study(
    frame: pl.DataFrame, events: list[str], *, horizons=STUDY_HORIZONS,
) -> dict:
    """For each event, compare conditional vs unconditional forward returns."""
    results: dict = {
        "experiment_version": ALPHA_EXPERIMENT_VERSION,
        "event_version": EVENT_DEFINITION_VERSION,
        "target_version": TARGET_VERSION,
        "horizons": list(horizons),
        "events": {},
    }
    for name in events:
        if name not in frame.columns:
            continue
        flag = frame[name].cast(pl.Boolean).fill_null(False).to_numpy()
        entry: dict = {"n_events": int(flag.sum()), "by_horizon": {}}
        for h in horizons:
            col = f"forward_return_{h}m"
            if col not in frame.columns:
                continue
            returns = frame[col].to_numpy()
            valid = np.isfinite(returns)
            ev = returns[valid & flag]
            uncond = returns[valid & ~flag]
            cond_stats = forward_distribution_stats(pl.Series("r", ev)) if ev.size else {"n": 0}
            uncond_stats = forward_distribution_stats(pl.Series("r", uncond)) if uncond.size else {"n": 0}
            test = _welch_diff_mean(ev, uncond) if ev.size >= 30 else {"diff": None, "stderr": None, "t": None}
            entry["by_horizon"][f"{h}m"] = {
                "conditional": cond_stats,
                "unconditional": uncond_stats,
                "test": test,
            }
        results["events"][name] = entry
    return results


def regime_conditional_study(
    frame: pl.DataFrame, events: list[str], *, horizons=(10, 30, 60),
    regimes: dict[str, str] | None = None,
) -> dict:
    """Report every regime, never a best one (Phase 20)."""
    regimes = regimes or {
        "volatility": "vol_regime",
        "trend": "trend_regime",
        "time_of_day": "hour_bucket",
    }
    out: dict = {"events": {}, "regime_definitions": regimes}
    for name in events:
        if name not in frame.columns:
            continue
        sub = frame.filter(pl.col(name) == True)  # noqa: E712
        if sub.is_empty():
            continue
        entry: dict = {}
        for regime_name, column in regimes.items():
            if column not in frame.columns:
                continue
            per: dict = {}
            for level in sorted(set(sub[column].drop_nulls().to_list())):
                bucket = sub.filter(pl.col(column) == level)
                stats: dict = {}
                for h in horizons:
                    col = f"forward_return_{h}m"
                    if col in frame.columns:
                        s = forward_distribution_stats(bucket[col])
                        stats[f"{h}m"] = {"n": s["n"], "mean": s["mean"], "median": s["median"]}
                per[str(level)] = stats
            entry[regime_name] = per
        out["events"][name] = entry
    return out


def add_regimes(frame: pl.DataFrame) -> pl.DataFrame:
    """Attach simple deterministic regime labels for conditioning."""
    import datetime as _dt
    minute = (pl.col("timestamp") // 60000) * 60000
    hour = ((pl.col("timestamp") // 3_600_000) % 24)
    ret = pl.col("close").pct_change()
    rv = ret.rolling_std(window_size=288, min_samples=60)
    rv_med = rv.rolling_median(window_size=288, min_samples=60)
    trend = (pl.col("close") - pl.col("close").rolling_mean(window_size=60, min_samples=20))
    return frame.with_columns(
        hour.alias("hour_bucket"),
        rv.alias("realized_vol"),
        (rv > rv_med).cast(pl.Int8).alias("vol_regime"),
        (trend > 0).cast(pl.Int8).alias("trend_regime"),
    )
