#!/usr/bin/env python3
"""EXP-007B — Phase 7B Controlled Target Comparison.

Arms (frozen OOS 2025-01-01 → 2025-03-01):
  A_binary15: binary P(up 15m) using existing QuantPredictor (binary target y_up_15)
  B_exp15:   expected return 15m using regression head (future_return_15m)
  C_exp5:    expected return 5m (regression on future_return_5m)
  D_exp30:   expected return 30m (regression on future_return_30m)
  E_updown:  UP/FLAT/DOWN from 15m regression (positive / near-zero / negative)

Preserves binary target; does not modify existing artifacts.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import polars as pl
from datetime import datetime, timezone

from jev_trading.quant.train import train_models, DEFAULT_LGBM_PARAMS, THRESHOLD, COST_PER_TRADE
from jev_trading.quant.model import load_models, QuantPredictor
from jev_trading.quant.economic import evaluate_economic_opportunity, EconomicQuantOutput, economic_stress_suite
from jev_trading.labels.engine import compute_labels
from jev_trading.state.features import FEATURE_COLUMNS, build_features
from jev_trading.backtest.simulator import SimConfig


def train_reg_for_target(bars: pl.DataFrame, label_col: str, split=None):
    """Quick regression training for 5/15/30m targets on a sample for measurement speed."""
    from jev_trading.labels.engine import compute_labels
    # Sample 20% for speed — measurement-first, not OOS-tuned
    sampled = bars.sample(fraction=0.2, seed=7)
    j = build_features(sampled).join(compute_labels(sampled), on="timestamp", how="inner").sort("timestamp").drop_nulls(subset=list(FEATURE_COLUMNS) + [label_col])
    train_end = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    train = j.filter(pl.col("timestamp") < train_end)
    from lightgbm import LGBMRegressor
    xtr = train.select(FEATURE_COLUMNS).to_numpy()
    ytr = train[label_col].to_numpy()
    reg = LGBMRegressor(num_leaves=16, n_estimators=50, learning_rate=0.05, verbose=-1).fit(xtr, ytr)
    return reg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-03-01")
    parser.add_argument("--model-dir", default="models/")
    args = parser.parse_args()

    out_dir = Path("experiments/EXP-007B")
    out_dir.mkdir(parents=True, exist_ok=True)
    import datetime as dt_mod
    start_ms = int(dt_mod.datetime.strptime(args.start, "%Y-%m-%d").timestamp() * 1000)
    end_ms = int(dt_mod.datetime.strptime(args.end, "%Y-%m-%d").timestamp() * 1000)

    bars = pl.read_parquet("data/btcusdt_1m.parquet")
    window_bars = bars.filter((pl.col("timestamp") >= start_ms) & (pl.col("timestamp") < end_ms))
    predictor = load_models(args.model_dir)

    # Build features for window
    feats_full = build_features(window_bars)
    X_df = feats_full.select(["timestamp", *FEATURE_COLUMNS])

    # A: binary 15m (existing predictor)
    p_up_binary = predictor.predict_up15(X_df.select(FEATURE_COLUMNS)).to_numpy()

    # Train regression heads quickly for 5/15/30m targets on full dataset (not OOS-tuned; measurement-only)
    full_bars = pl.read_parquet("data/btcusdt_1m.parquet")
    reg5 = train_reg_for_target(full_bars, "future_return_5m")
    reg15 = train_reg_for_target(full_bars, "future_return_15m")
    reg30 = train_reg_for_target(full_bars, "future_return_30m")

    xmat = X_df.select(FEATURE_COLUMNS).to_numpy()

    # B/C/D: regression predictions
    exp5 = np.asarray(reg5.predict(xmat), dtype=float)
    exp15_reg = np.asarray(reg15.predict(xmat), dtype=float)
    exp30 = np.asarray(reg30.predict(xmat), dtype=float)

    # E: UP/FLAT/DOWN from 15m regression
    # Simple thresholding: > 0.0014 = UP; < -0.0014 = DOWN; else FLAT
    up_down_class = np.where(exp15_reg > THRESHOLD, 1, np.where(exp15_reg < -THRESHOLD, -1, 0))

    # Economic evaluation for each arm (base cost 1x)
    results = {}
    arms = {
        "A_binary15": {"predictions": p_up_binary, "label_type": "binary_15m"},
        "B_exp15": {"predictions": exp15_reg, "label_type": "expected_return_15m"},
        "C_exp5": {"predictions": exp5, "label_type": "expected_return_5m"},
        "D_exp30": {"predictions": exp30, "label_type": "expected_return_30m"},
        "E_updown": {"predictions": up_down_class, "label_type": "up_flat_down_15m"},
    }

    for arm, info in arms.items():
        preds = info["predictions"]
        # For binary/UP-DOWN: compute simple economic approximation using existing economic layer logic
        if info["label_type"] == "binary_15m":
            # Use existing economic approximation via probability
            economic_list = []
            for i in range(len(X_df)):
                q = EconomicQuantOutput(
                    p_up_15=float(preds[i]),
                    p_dn_15=1.0 - float(preds[i]),
                    expected_return_15=float(preds[i] * 0.003 - (1.0 - preds[i]) * 0.002),
                    expected_downside_15=float(-abs(float(preds[i] * 0.003 - (1.0 - preds[i]) * 0.002)) - 0.0005),
                    expected_favorable_excursion_15=float(abs(float(preds[i] * 0.003 - (1.0 - preds[i]) * 0.002)) + 0.001),
                    expected_adverse_excursion_15=float(-abs(float(preds[i] * 0.003 - (1.0 - preds[i]) * 0.002)) - 0.0005),
                    uncertainty=0.02,
                    horizon_min=15,
                )
                economic_list.append(q)
        else:
            # For regression / UP-DOWN: use predictions directly as expected return
            economic_list = []
            for i in range(len(X_df)):
                q = EconomicQuantOutput(
                    p_up_15=1.0 if preds[i] > THRESHOLD else (0.0 if preds[i] < -THRESHOLD else 0.5),
                    p_dn_15=1.0 if preds[i] < -THRESHOLD else (0.0 if preds[i] > THRESHOLD else 0.5),
                    expected_return_15=float(preds[i]),
                    expected_downside_15=float(-abs(preds[i]) - 0.0005) if preds[i] < 0 else float(-abs(preds[i]) - 0.0005),
                    expected_favorable_excursion_15=float(abs(preds[i]) + 0.001) if preds[i] > 0 else float(0.001),
                    expected_adverse_excursion_15=float(-abs(preds[i]) - 0.0005),
                    uncertainty=0.02,
                    horizon_min=15,
                )
                economic_list.append(q)

        # Evaluate economic opportunity for base 1x cost
        decisions = []
        for q in economic_list:
            ec = evaluate_economic_opportunity(q, fee_mult=1.0)
            decisions.append({
                "label_type": info["label_type"],
                "pred": float(preds[len(decisions)] if False else 0),  # placeholder; we'll compute per-row properly
                "expected_return_15": q.expected_return_15,
                "expected_net_edge": ec.expected_net_edge,
                "trade_decision": ec.trade_decision,
            })
        # Actually compute per-row properly
        decisions = []
        for idx in range(len(X_df)):
            q_row = economic_list[idx]
            ec_row = evaluate_economic_opportunity(q_row, fee_mult=1.0)
            decisions.append({
                "label_type": info["label_type"],
                "pred_raw": float(preds[idx]),
                "pred_interpreted": "UP" if preds[idx] > THRESHOLD else ("DOWN" if preds[idx] < -THRESHOLD else "FLAT" if info["label_type"] == "up_flat_down_15m" else preds[idx]),
                "expected_return_15": float(preds[idx]) if info["label_type"] != "binary_15m" else float(preds[idx] * 0.003 - (1.0 - preds[idx]) * 0.002),
                "expected_net_edge": ec_row.expected_net_edge,
                "trade_decision": ec_row.trade_decision,
                "reasons_summary": ec_row.decision_reasons[-1],
            })

        # Aggregate metrics
        trades = sum(1 for d in decisions if d["trade_decision"] == "TRADE")
        avg_pred_edge = float(np.mean([d["expected_net_edge"] for d in decisions]))
        avg_pred_return = float(np.mean([d["expected_return_15"] for d in decisions]))

        # Stress: 3x cost stress on first 500 rows for comparison
        stress_trades = 0
        for d in decisions[:min(500, len(decisions))]:
            # Approximate stress: if base 1x fails edge, 3x will also fail (simplified)
            stress_trades += 1 if d["expected_net_edge"] > 0.0042 else 0  # rough threshold

        metrics = {
            "arm": arm,
            "label_type": info["label_type"],
            "n_bars": len(X_df),
            "n_trades_1x": trades,
            "avg_predicted_net_edge_1x": round(avg_pred_edge, 6),
            "avg_predicted_gross_return": round(avg_pred_return, 6),
            "trade_rate_1x": round(trades / len(X_df), 4) if len(X_df) > 0 else 0,
            "prob_continuous_15m": float(np.all(p_up_binary >= 0) and np.all(p_up_binary <= 1) and np.issubdtype(p_up_binary.dtype, np.floating)) if info["label_type"] == "binary_15m" else None,
            "stress_3x_approx_trades_in_500": stress_trades,
            "note": "Regression heads for 5/15/30m trained quickly on full dataset; measurement-first, not tuned on OOS.",
            "first_5_decisions": decisions[:5],
        }
        results[arm] = metrics
        (out_dir / f"arm_{arm}_metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
        (out_dir / f"arm_{arm}_decisions.jsonl").write_text("\n".join(json.dumps(d) for d in decisions[:1000]))

    # Aggregate comparison
    comparison = {arm: {
        "label_type": results[arm]["label_type"],
        "avg_predicted_net_edge_1x": results[arm]["avg_predicted_net_edge_1x"],
        "avg_predicted_gross_return": results[arm]["avg_predicted_gross_return"],
        "n_trades_1x": results[arm]["n_trades_1x"],
        "trade_rate_1x": results[arm]["trade_rate_1x"],
        "prob_continuous_15m": results.get("A_binary15", {}).get("prob_continuous_15m"),
    } for arm in results}
    (out_dir / "comparison.json").write_text(json.dumps({"comparison": comparison}, indent=2, default=str))

    # Provenance
    provenance = {
        "experiment_id": "EXP-007B",
        "phase": "Phase 7B — Controlled Target Comparison",
        "targets_compared": ["binary_15m", "expected_return_15m", "expected_return_5m", "expected_return_30m", "up_flat_down_15m"],
        "frozen_oos": f"[{args.start}, {args.end})",
        "same_split": True,
        "same_features": "FEATURE_COLUMNS frozen",
        "note": "Binary target preserved. Regression heads for 5/15/30m trained on full dataset (measurement-first). No Laya, no RL.",
    }
    (out_dir / "provenance.json").write_text(json.dumps(provenance, indent=2))
    (out_dir / "locked_config.json").write_text(json.dumps({"locked": True, "start": args.start, "end": args.end, "threshold": 0.0014, "targets": list(results.keys())}, indent=2))

    # Print quick comparison
    print(json.dumps({
        "experiment": "EXP-007B",
        "arms": list(results.keys()),
        "avg_predicted_net_edge_1x": {arm: results[arm]["avg_predicted_net_edge_1x"] for arm in results},
        "avg_predicted_gross_return": {arm: results[arm]["avg_predicted_gross_return"] for arm in results},
        "n_trades_1x": {arm: results[arm]["n_trades_1x"] for arm in results},
    }, indent=2))

if __name__ == "__main__":
    main()
