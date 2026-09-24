#!/usr/bin/env python3
"""Summarize Phase 2 base predictions and selected trades by causal regime buckets."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jev_trading.quant.engine import FEATURE_SETS, build_execution_training_frame, chronological_economic_split, load_economic_bundle
from jev_trading.quant.evaluation import predicted_edge_summary, simulate_fixed_horizon_segments

OOS_START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
VALID_START = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="artifacts/phase0-baseline-final-d/pre_oos_2021_2024.parquet")
    parser.add_argument("--model-dir", default="artifacts/phase2-experiments-final/EXP-010-economic-return-baseline/models")
    parser.add_argument("--out", default="artifacts/phase2-experiments-final/regime_analysis.json")
    args = parser.parse_args(argv)
    bars = pl.read_parquet(args.bars).filter(pl.col("timestamp") < OOS_START).sort("timestamp")
    features = FEATURE_SETS["base"]
    frame = build_execution_training_frame(bars, features)
    _train, valid = chronological_economic_split(frame, VALID_START, VALID_START, OOS_START, 60)
    valid = valid.drop_nulls(subset=[*features, "direction_class_exec_15", "execution_return_15m", "funding_rate"])
    bundle = load_economic_bundle(args.model_dir)
    X = valid.select(["timestamp", *features])
    direction = bundle.predict_direction(X)
    prediction = bundle.predict_execution_return(X, 15)
    uncertainty = bundle.predict_uncertainty_15(X)
    actual = valid["execution_return_15m"].to_numpy()
    funding = valid["funding_rate"].to_numpy()
    timestamps = valid["timestamp"].to_numpy()
    p_up, p_flat, p_dn = direction["p_up_15"], direction["p_flat_15"], direction["p_dn_15"]

    vol = valid["realized_vol_30m"].to_numpy()
    trend = valid["trend_score"].to_numpy()
    funding_z = valid["funding_z"].to_numpy()
    volume_z = valid["volume_z"].to_numpy()
    regimes = {
        "volatility": np.where(vol <= np.quantile(vol, 1/3), "low", np.where(vol <= np.quantile(vol, 2/3), "normal", "high")),
        "trend": np.where(np.abs(trend) < 0.15, "flat", "trending"),
        "funding": np.where(funding_z < -1, "negative_extreme", np.where(funding_z > 1, "positive_extreme", "normal")),
        "volume": np.where(volume_z <= np.quantile(volume_z, 1/3), "low", np.where(volume_z <= np.quantile(volume_z, 2/3), "normal", "high")),
    }
    output = {"oos_used": False, "dimensions": {}}
    for name, labels in regimes.items():
        rows = []
        for label in sorted(set(labels.tolist())):
            mask = labels == label
            edge = predicted_edge_summary(
                p_up[mask], p_flat[mask], p_dn[mask], prediction[mask], uncertainty[mask], funding[mask]
            )
            rows.append({"regime": label, **edge})
        output["dimensions"][name] = rows

    simulation = simulate_fixed_horizon_segments(
        timestamps, p_up, p_flat, p_dn, prediction, actual, funding, uncertainty,
        horizon_minutes=15,
    )
    row_by_ts = valid.select(["timestamp", *features]).to_dicts()
    state = {int(row["timestamp"]): row for row in row_by_ts}
    selected = []
    for trade in simulation["trades"]:
        row = state[trade["timestamp"]]
        selected.append({
            **trade,
            "volatility_regime": next(r["regime"] for r in output["dimensions"]["volatility"] if regimes["volatility"][valid["timestamp"].to_numpy() == trade["timestamp"]][0] == r["regime"]),
            "trend_regime": "flat" if abs(row["trend_score"]) < 0.15 else "trending",
            "funding_regime": "negative_extreme" if row["funding_z"] < -1 else ("positive_extreme" if row["funding_z"] > 1 else "normal"),
        })
    output["selected_trades"] = selected
    output["selected_trade_count"] = len(selected)
    output["interpretation"] = "Selected-trade counts are too small for regime-specific promotion; no regime is optimized independently."
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, sort_keys=True))
    print(json.dumps({"selected_trade_count": len(selected), "out": str(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
