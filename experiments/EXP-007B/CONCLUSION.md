# EXP-007B — Phase 7B Target Comparison Conclusion

## Evidence (reproducible artifacts in this directory)

- `arm_A_binary15`: avg gross return ≈ `-0.00074`; trades under economic gate = 0; probability continuous verified.
- `arm_B_exp15`: avg gross return ≈ `0.596`; economic gate produces `84960` trades (false positive due to uncalibrated regression).
- `arm_C_exp5`: avg gross return ≈ `0.623`; same false positive pattern.
- `arm_D_exp30`: avg gross return ≈ `0.618`; same false positive pattern.
- `arm_E_updown`: avg gross return ≈ `1.0`; complete calibration failure.

## Finding: Model/Target Limitation (Outcome 3)

The regression predictions (`expected_return_5m/15m/30m`) are not calibrated to realistic economic magnitudes. They predict gross returns of ~60% over 15 minutes (`0.596`), which is impossible for BTCUSDT 1m bars. This causes the economic layer (`expected_net_edge`) to incorrectly approve trades (`TRADE`) for nearly all predictions, producing false positive economic signals.

The binary target (`P(up 15m)`) remains the only calibrated prediction method on frozen OOS, but it predicts negative economic edge (`-0.000734`), confirming Phase 6's NO-GO.

This demonstrates a clear modeling limitation: the regression architecture (light LGBM with same `FEATURE_COLUMNS`) does not learn economically realistic return magnitudes. Before any specialist strategy or Laya layer is added, the predictive model must either:
1. Be recalibrated to realistic return scales (e.g., scaled targets, log-transform, or calibrated regression output), or
2. Use a different modeling approach that predicts economically realistic magnitudes.

A predictive metric improvement (e.g., lower MSE on raw future return) does not guarantee economic viability. The gate for the next phase remains economic (`positive net edge under 1x + 2x stress`), not predictive.
