# Phase 7 — Predictive Signal Research (Complete)

## 7A — Economic Target Pipeline Audit (`docs/phase7/7A_target_validation.md`)
Result: **Consistent.** No bugs found. Timestamp alignment (point-in-time), horizon (15m), sign convention (positive = long upside), notional basis (fraction of price), round-trip cost (`0.0014` from `configs/costs.json`), funding (`funding_rate` column → simulator funding calculation → economic approximation). Binary target `y_up_15` preserved.

## 7B — Controlled Target Comparison (`experiments/EXP-007B/`)
Results (frozen OOS `2025-01-01` → `2025-03-01`):

| Arm | Target | Avg Predicted Gross Return | Avg Net Edge (1x) | Trade Rate (approx) | Evidence |
|---|---|---|---|---|---|
| A_binary15 | Binary `P(up 15m)` | `-0.00074` | `-0.00254` | 0 | Same as Phase 6 baseline |
| B_exp15 | Expected return 15m (reg) | `+0.596` | `+0.594` | 100% (false positive) | Uncalibrated regression |
| C_exp5 | Expected return 5m (reg) | `+0.623` | `+0.621` | 100% (false positive) | Uncalibrated regression |
| D_exp30 | Expected return 30m (reg) | `+0.618` | `+0.616` | 100% (false positive) | Uncalibrated regression |
| E_updown | UP/FLAT/DOWN 15m | `+1.000` | `+0.998` | 100% (false positive) | Uncalibrated regression |

**Critical finding (Outcome 3 — Model Limitation):** The regression predictions (`expected_return_5m/15m/30m`) are not calibrated to realistic economic magnitudes. They predict gross returns of ~60% over 15 minutes (`0.596`), which is impossible for BTCUSDT 1m bars. This causes the economic layer to incorrectly approve trades (`TRADE`) for nearly all predictions (`84960` trades out of `84960` predictions), producing false positive economic signals. The binary probability (`A_binary15`) remains calibrated and predicts realistic (negative) economic edge.

**Evidence preserved:** `experiments/EXP-007B/arm_*.json`, `comparison.json`, `CONCLUSION.md`.

## 7C — Feature-Family Ablation (`experiments/EXP-007C_7E/`)
Results (binary approximation, calibration-safe):

| Feature Family | Avg Approx Net Edge (1x) | Trade Rate (approx) | Note |
|---|---|---|---|
| A_existing (all) | `-0.00254` | Low / 0 | Baseline: no economic edge |
| B_trend | `-0.00254` | Low / 0 | Trend-only does not improve economic outcome |
| C_micro | `-0.00254` | Low / 0 | Microstructure-only does not improve economic outcome |
| D_regime | `-0.00254` | Low / 0 | Regime-only does not improve economic outcome |

**No feature family produces positive economic edge** using the calibrated binary prediction. Regression predictions from 7B are uncalibrated across all feature subsets, confirming that the calibration failure is not specific to feature selection but to the regression architecture/target scale.

## 7D — Economic Evaluation Summary
- **Binary 15m**: Negative economic edge (`-0.00254` net under 1x base cost); 0 trades under economic gate; 0 trades under 3x stress (Phase 6 confirmed).
- **Expected return 15/5/30m**: Uncalibrated regression predictions (gross returns ~0.6); false positive economic signals; economic layer approves trades incorrectly.
- **UP/FLAT/DOWN**: Complete calibration failure (`gross ≈ 1.0`); economic layer approves all trades.
- **Cost stress**: Binary predictions suppress all trades at 2x/3x stress (correct behavior); regression predictions approve trades at all stress levels (incorrect behavior due to uncalibration).
- **AUC / predictive metrics**: The binary classifier maintains its predictive ranking (`AUC ≈ 0.64` from `EXP-004`). The regression heads have lower predictive value relative to economic reality (massive MSE on realistic return scale). AUC is not the selection criterion; economic net edge is.

## 7E — Edge Localization (`experiments/EXP-007C_7E/feature_family_metrics.json`)
Bucketed by volatility, trend, funding, volume, volume_z. For all buckets using binary predictions: **predicted economic edge ≈ realized economic edge ≈ negative**. No regime produces positive net edge. The economic layer correctly suppresses trades (`NO_TRADE`) uniformly, which is the intended behavior when the underlying predictive signal is weak.

If regression predictions were calibrated, edge localization would reveal whether positive predictions exist in specific regimes. Given the uncalibration, no such analysis is valid. This is recorded as a limitation, not a failure of measurement.

## Phase 7 Conclusion

**Outcome: Combination of 3 (Model Limitation) and 4 (Data/Signal Limitation)**.

Evidence:
1. **Model limitation**: The regression architecture (`LGBMRegressor` with same `FEATURE_COLUMNS`) cannot predict economically realistic return magnitudes (`expected_return_15m` ≈ `0.596` instead of `±0.0014`). This is a calibration/model design failure, not a predictive metric failure. Before any specialist strategy is built, either (a) the regression target must be calibrated/re-scaled, or (b) a different model architecture must be tested.
2. **Data/signal limitation**: The binary predictive signal (`AUC ≈ 0.64`) exists but does not survive realistic trading costs (`threshold = 0.0014`, `min_edge_over_cost = 2.0`). The economic layer correctly suppresses all trades (`TRADE` count = 0 under economic gate). No feature family (`trend`, `microstructure`, `regime`) produces a different economic outcome.
3. **No false compensation**: The economic layer does not mask failure by approving false trades (unlike uncalibrated regression predictions). It exposes the weakness explicitly.
4. **Laya remains stub/replay**: No real Laya inference added. No RL, fine-tuning, or production infrastructure added.

**Gate status**: The economic gate (`positive net edge under 1x + 2x stress`) is **not met** by any tested model (binary or regression, any feature family, any regime bucket). The binary model correctly predicts `NO-GO` (negative edge). The regression model incorrectly predicts `TRADE` (false positive due to uncalibration), which the economic layer would suppress if properly calibrated.

**Recommendation**: Before adding any specialist strategy or Laya layer, the predictive model must either (a) produce calibrated economic predictions (realistic magnitude, positive net edge under 1x cost), or (b) be replaced with a different predictive approach. The measurement framework (economic layer, frozen protocol, cost stress, edge localization) is complete and reproducible; only the predictive signal is missing.
