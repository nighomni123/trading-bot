"""Stage 10 geometry search against measured cost profiles.

The Stage 9 result gives a decisive short-circuit: a barrier geometry is only
worth a trained model if its MAXIMUM achievable gross
(`target - p_stop*stop`, the oracle bound at p_target=1) exceeds the all-in
cost. This module computes that bound across a pre-registered grid, then only
runs the model where the bound is not already impossible.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import numpy as np
import polars as pl

from jev_trading.labels.live_barrier import LONG, SHORT, TARGET_FIRST, atr_fraction, build_live_barrier_labels
from jev_trading.quant.execution_economics import CostMeasurement, build_profiles, measure_book, profile_total_bps

# Pre-registered geometry grid. Fixed before any result is observed.
GRID = tuple(
    (target_atr, stop_atr, horizon)
    for target_atr in (0.5, 1.0, 2.0, 3.0, 5.0)
    for stop_atr in (0.5, 1.0, 2.0)
    for horizon in (15, 30, 60, 120, 240, 480)
)


def geometry_bound(
    bars: pl.DataFrame, target_atr: float, stop_atr: float, horizon: int, *,
    side: int = LONG,
) -> dict[str, Any]:
    """Oracle bound and realized statistics for one geometry (no model)."""
    atr = atr_fraction(bars)
    labels = build_live_barrier_labels(bars, atr * target_atr, atr * stop_atr, side, horizon)
    frame = labels.with_columns(
        target_fraction=(atr * target_atr).alias("target_fraction"),
        stop_fraction=(atr * stop_atr).alias("stop_fraction"),
    ).filter(pl.col("outcome").is_not_null())

    if frame.height == 0:
        return {"n": 0}
    outcome = frame["outcome"].to_numpy()
    p_target = float((outcome == TARGET_FIRST).mean())
    p_stop = float((outcome == 0).mean())  # STOP_FIRST
    target_bps = float(frame["target_fraction"].median()) * 1e4
    stop_bps = float(frame["stop_fraction"].median()) * 1e4

    # Oracle bound: best case p_target=1, p_timeout=0, p_stop unchanged.
    max_gross = target_bps - p_stop * stop_bps
    return {
        "n": frame.height,
        "target_bps": target_bps,
        "stop_bps": stop_bps,
        "p_target": p_target,
        "p_stop": p_stop,
        "p_timeout": 1.0 - p_target - p_stop,
        "max_achievable_gross_bps": max_gross,
        "geometry": f"{target_atr:g}:{stop_atr:g}@{horizon}m",
    }


def viability_matrix(
    bars: pl.DataFrame, *, profiles, measurement: CostMeasurement | None = None,
    side: int = LONG, grid=GRID,
) -> list[dict[str, Any]]:
    """For each geometry and cost profile: is the oracle bound above cost?"""
    out = []
    for target_atr, stop_atr, horizon in grid:
        bound = geometry_bound(bars, target_atr, stop_atr, horizon, side=side)
        if bound["n"] == 0:
            continue
        entry = {**bound, "costs": {}}
        for profile in profiles:
            total = profile_total_bps(profile, measurement)
            entry["costs"][profile.name] = {
                "total_bps": total,
                "max_gross_minus_cost_bps": bound["max_achievable_gross_bps"] - total,
                "oracle_viable": bound["max_achievable_gross_bps"] > total,
            }
        out.append(entry)
    return out


def run_geometry_search(
    bars: pl.DataFrame, *, measurement: CostMeasurement | None = None,
    side: int = LONG, grid=GRID,
) -> dict[str, Any]:
    profiles = build_profiles(measurement)
    rows = viability_matrix(bars, profiles=profiles, measurement=measurement, side=side, grid=grid)

    achievable = [p for p in profiles if p.achievable]
    feasible = [
        row for row in rows
        if any(row["costs"][p.name]["oracle_viable"] for p in achievable)
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "side": "LONG" if side == LONG else "SHORT",
        "grid_cells": len(rows),
        "cost_profiles": {p.name: profile_total_bps(p, measurement) for p in profiles},
        "achievable_profiles": [p.name for p in achievable],
        "rows": rows,
        "oracle_feasible_geometries": len(feasible),
        "best_feasible": max(
            feasible,
            key=lambda r: max(r["costs"][p.name]["max_gross_minus_cost_bps"] for p in achievable),
            default=None,
        ),
        "execution": "NOT_INVOKED",
    }
