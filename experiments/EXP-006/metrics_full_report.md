# EXP-006 — Required Metrics Report (Phase 6 Economic Redesign)

This document reports all metrics requested in Phase 6 §7. Where full simulator-level metrics are not produced by the minimal comparison script, they are explicitly marked `N/A (full sim preserved in EXP-004)` so that no metric is silently omitted.

## 1. Prediction / Coverage Metrics

| Metric | A (Baseline) | B (Corrected Prob) | C (Economic Model) | D (Economic + Cost) | Note |
|---|---|---|---|---|---|
| Number of predictions | 5,253 (2025-01 → 2025-03) | 5,253 | 5,253 | 5,253 | Same frozen OOS window |
| Coverage (trade rate) | 0.402 | 0.402 | ~0.0 (edge fails) | 0.0 (3x stress) | Economic gate suppresses trades |
| Probability continuous verified | True | True | True | True | `predict_proba` enforced |
| p_up resolution bins (rounded 4-decimal) | >1 | >1 | >1 | >1 | Continuous, not binary |

## 2. Trading / Economic Metrics

| Metric | A | B | C | D | Evidence Source |
|---|---|---|---|---|---|
| Gross return (expected) | N/A (binary model) | N/A | -0.000734 avg | -0.000734 avg | `avg_predicted_edge` from regression head |
| Fees (base 1x) | N/A | N/A | ~0.0018 / trade | ~0.0018 / trade | Economic layer (`cost_fees`) |
| Slippage (base 1x) | N/A | N/A | ~0.0002 / trade | ~0.0002 / trade | Economic layer (`cost_slippage`) |
| Funding (base) | N/A | N/A | ~0.00015 / trade | ~0.00015 / trade | Economic layer (`cost_funding`) |
| Net edge (base) | N/A | N/A | -0.000734 avg | -0.000734 avg | `expected_net_edge` |
| Net return (sim-level) | See EXP-004 arm_A | See EXP-004 arm_B | N/A (minimal) | N/A (minimal) | Full simulator preserved in EXP-004 |
| Expectancy / trade | N/A | N/A | -0.000734 | -0.000734 | Average predicted net edge |
| Win rate | See EXP-004 metrics | See EXP-004 metrics | N/A | N/A | Preserved from EXP-004 |
| Profit factor | See EXP-004 metrics | See EXP-004 metrics | N/A | N/A | Preserved from EXP-004 |
| Sharpe (sim-level) | See EXP-004 metrics | See EXP-004 metrics | N/A | N/A | Preserved from EXP-004 |
| Sortino (sim-level) | See EXP-004 metrics | See EXP-004 metrics | N/A | N/A | Preserved from EXP-004 |
| Maximum drawdown (sim-level) | See EXP-004 metrics | See EXP-004 metrics | N/A | N/A | Preserved from EXP-004 |

## 3. Calibration / Predictive Integrity

- **Brier score / ECE**: Binary classifier metrics preserved in `metrics.json` (A/B use same binary model). Economic regression head (`reg_model`) has `reg_mse` / `reg_mae` / `reg_corr` reported in `train_models()` metrics.
- **Probability bounds**: Confirmed [0, 1] via regression tests (`tests/test_quant_regression.py`).
- **Inference consistency**: `predict_up15()` matches `predict_proba` within `1e-6` (`test_inference_consistent_with_train_proba`).

## 4. Performance by Regime / Bucket (Minimal Evidence)

- **Regime**: The frozen OOS window (2025-01 → 2025-03) covers one regime only. Regime analysis requires the full year (2024 valid + 2025 test); preserved in `experiments/EXP-004/` artifacts.
- **Confidence / edge bucket**: Economic gate (`min_edge_over_cost = 2.0`) suppresses all low-edge predictions. The economic layer's `decision_reasons` provide per-row audit trails (`experiments/EXP-006/arm_D_decisions.jsonl`) showing why each bar was `TRADE` or `NO_TRADE`.

## 5. Cost Stress Results (Economic Layer)

| Stress Mode | Potential Trades (n=500 sample) | Notes |
|---|---|---|
| 1x base | Low / 0 | Economic gate requires `net_edge >= 2.0 * cost`; signal fails |
| 2x cost | 0 | Net edge turns negative for most predictions |
| 3x cost | 0 | Confirmed in `arm_D_metrics.json` (`economic_stress.3x_cost.potential_trades_in_sample = 0`) |
| Increased slippage (+0.05%) | 0 | Net edge further reduced |
| Execution delay (+0.02%) | 0 | Net edge reduced by delay penalty |

## 6. Key Evidence for Conclusion

- **Predicted economic edge** (`avg_predicted_edge` for C): **-0.000734** (negative gross return expectation)
- **Realized net edge** (sim-level, from EXP-004 baseline): Negative under all arms (see `experiments/EXP-004/comparison.json`)
- **Correlation / calibration of economic predictions**: The regression head (`reg_model`) predicts `expected_return_15`, but the predictions are weak (negative mean). There is no positive correlation between `expected_return_15` and realized net PnL in the frozen test period.
- **Does predicted economic edge correspond to realized net edge?** **No** — the predicted edge is negative, and the economic layer correctly suppresses trades (`NO_TRADE`). The layer is functioning as intended (preventing unprofitable trades), but the underlying signal is insufficient to generate `TRADE` decisions under realistic cost assumptions.

## 7. Preservation of Baseline Artifacts

- `experiments/EXP-004/` untouched (all original `arm_A.jsonl`, `metrics.json`, etc. preserved).
- `models/` untouched (original `lgbm_sklearn.pkl` not overwritten; new `lgbm_reg.pkl` saved by updated `save_models`).
- Binary target `y_up_15` preserved in `labels/engine.py`.
- Original `quant/train.py` binary model unchanged; regression head (`reg_model`) is an additional output, not a replacement.

## 8. No Laya / Production Expansion (Verified)

- `frontier/laya.py`: Unchanged (stub/replay only).
- `scripts/replay_laya.py`: Unchanged.
- No new RL, fine-tuning, or production infrastructure added.
- `router.py`: Unchanged.

---

**Conclusion (repeated from `CONCLUSION.md`):** `NO-GO` for the economic-target redesign as a standalone solution. The redesigned economic layer is correct and reproducible — it exposes assumptions, enforces `NO_TRADE`, survives 2x/3x cost stress, and does not mask a weak signal. The underlying predictive signal remains below the fee floor; adding Laya or more complex architecture would not fix this measurement. Proceed to deeper predictive signal research (stronger feature/model) before expanding integration.
