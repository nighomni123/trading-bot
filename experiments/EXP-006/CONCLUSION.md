# EXP-006 Phase 6 Conclusion

## Evidence
- Probability inference continuous verified: True
- Expected gross edge (arm C avg): -0.000734
- Trade count under 3x cost stress (arm D): 0
- Economic layer: deterministic, assumptions exposed
- Raw returns preserved: future_return_5m/15m/30m/60m + excursions retained
- Binary target y_up_15 preserved for baseline comparison
- NO_TRADE first-class: positive direction alone insufficient; economic gate required
- Risk kernel authority preserved
- Laya integration: unchanged (stub/replay only)

## Conclusion: NO-GO

Supporting analysis:
- The redesigned economic layer measures expected gross return, net edge, downside probability, excursions, and uncertainty.
- Under 1x base cost, the economic gate requires net_edge >= 2.0 * cost (from policy config). The signal's average predicted gross return (-0.000734) does not consistently survive this gate, leading to NO_TRADE decisions for most bars.
- Under 3x hostile cost stress, trade count collapses (sample trades = 0), confirming cost sensitivity.
- The underlying predictive signal (AUC ~0.64 from prior audits) exists but is weak; the economic layer correctly prevents unprofitable trades rather than masking failure with more complex models.
- Recommendation: Proceed to deeper signal research if a stronger predictive model is available; do NOT add Laya for this weak signal.
