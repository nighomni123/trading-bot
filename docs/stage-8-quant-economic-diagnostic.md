# Stage 8 — Quant Economic Edge Diagnostic

Read-only diagnostic. No threshold, cost, horizon, R:R, quant, or risk parameter was changed. No LLM call. No execution.

```text
data:    data/btcusdt_1m.parquet (2,629,440 one-minute bars, 2021-01-01 .. 2025-12-31)
slice:   30 evenly spaced evaluation candidates (3-day spacing) + 60 calibration candidates
command: python -m jev_trading.live_intelligence quant-diagnostic
artifacts: docs/quant-economic-diagnostic-2026-09-25.json
```

## Q1 — Why are all candidates rejected?

Every candidate, without exception, is rejected by exactly one rule:

```text
net_expected_value_below_minimum   30 / 30
insufficient_path_samples           0
missing_economic_value              0
liquidity_below_minimum             0
```

`PolicyDecision.reasons` already carried these codes; they had simply never been aggregated. There is no hidden veto.

Per-candidate decomposition (first 6 of 30), all values in bps:

| side | ATR | target | stop | p_target | p_stop | p_timeout | gross | net | required p_target | obs/req | realized |
|---|---|---|---|---|---|---|---|---|---|---|---|
| LONG | 18.2 | 36.5 | 18.2 | 0.010 | 0.188 | 0.802 | +0.09 | −14.94 | 0.468 | 0.021 | +2.17 |
| SHORT | 17.8 | 35.7 | 17.8 | 0.030 | 0.298 | 0.672 | −0.79 | −15.78 | 0.547 | 0.055 | +15.08 |
| LONG | 41.6 | 83.2 | 41.6 | 0.012 | 0.098 | 0.890 | −4.50 | −19.50 | 0.242 | 0.050 | −28.87 |
| LONG | 27.1 | 54.2 | 27.1 | 0.088 | 0.222 | 0.690 | +4.44 | −10.55 | 0.318 | 0.277 | −21.58 |
| SHORT | 24.3 | 48.5 | 24.3 | 0.018 | 0.284 | 0.698 | −1.54 | −16.54 | 0.411 | 0.044 | +6.99 |
| LONG | 19.3 | 38.6 | 19.3 | 0.086 | 0.292 | 0.622 | +0.18 | −14.82 | 0.514 | 0.167 | −32.58 |

## Q2 — Required vs observed target probability

```text
median observed / required:  0.103
range:                        0.017 .. 0.325
median observed p_target:    0.023
```

The system reaches roughly **one tenth** of the probability it needs. This is an order-of-magnitude gap, not a marginal one. No threshold adjustment closes it.

## Q3 — Barrier geometry and horizon sweep

Median net expected value in bps. `pos` = candidates with net > 0.

| horizon | 1:1 | | 2:1 (current) | | 3:1 | |
|---|---|---|---|---|---|---|
| | net | pos | net | pos | net | pos |
| 15m | −10.93 | 1 | **−15.12** | 0 | −15.63 | 0 |
| 30m | −6.64 | 5 | −15.19 | 0 | −15.92 | 0 |
| 60m | −5.62 | 7 | −16.24 | 3 | −17.36 | 1 |
| 120m | −6.99 | 5 | −16.36 | 3 | −22.31 | 0 |
| 240m | **−4.34** | 5 | −16.09 | 0 | −26.92 | 0 |

Two structural facts:

1. **The current 2:1 geometry is the worst-performing column at every horizon.** p_target collapses to 0.000–0.012 at 15m because a 2×ATR target inside 15 minutes is simply rarely touched. 1:1 dominates 2:1 by 4–11 bps at every horizon.
2. **Longer horizons reduce cost drag but do not create positive median net value.** The best cell (240m, 1:1) is still −4.34 bps.

`p_timeout` falls from 0.80 at 15m/2:1 to 0.00 at 240m/1:1 — the trade-off is fewer timeouts but a wider stop.

## Q4 — Cost decomposition

```text
fees       10.00 bps   (2 × 5.0 bps/side)
slippage    4.00 bps   (2 × 2.0 bps/side)
latency     1.00 bps
funding     0.01 bps
------------------
TOTAL      15.01 bps  = 0.150% per trade

median gross:  −0.32 bps
max gross:     +6.20 bps
```

**Costs are not the primary cause.** Median gross is already negative before the cost stack is applied. The cost stack destroys a ~0 bps opportunity, not a positive one. Halving costs would not produce a single trade at 15m/2:1.

Empirical close→next-open friction measured from the dataset is ~0.0 bps (this parquet is clean bar data with no bid/ask), so it cannot independently confirm the 15 bps assumption. The configured figure is an assumption, not a measurement, and remains unvalidated — but it is not what is blocking trades.

## Q5 — Calibration (the most important finding)

60 candidates bucketed by predicted p_target:

| bucket | n | median predicted | **realized hit rate** | realized MFE | realized MAE | realized return |
|---|---|---|---|---|---|---|
| 0.00–0.02 | 28 | 0.008 | **0.000** | +7.97 | −5.78 | −3.00 |
| 0.02–0.05 | 20 | 0.030 | **0.000** | +6.56 | −6.87 | −2.67 |
| 0.05–0.10 | 12 | 0.061 | **0.000** | +3.32 | −8.65 | −3.27 |

**Realized target-hit rate is 0.000 in every bucket, including the highest predicted one.** Realized MFE is 3–8 bps — far below the 36–83 bps targets the candidates specify. Realized return is negative in every bucket.

The predicted p_target is not merely too low. It is **not monotone in any realized quantity**, and even the top bucket never came close to its target. Across all candidates the median realized return is −0.96 bps against a 15 bps cost stack.

## Q6–Q9 — Verdict

**Case B/C: geometry/estimator, not costs, not calibration-of-a-good-model.**

- The `p_target` estimate is produced by counting historical barrier hits in a trailing 3000-bar window (`build_completed_path_samples` → `analyze_path`). It is not a trained model. The LightGBM artifacts exist only in frozen `artifacts/phase*/models/`; nothing in `live_intelligence/quant/` loads one.
- The estimator reports p_target of 0.01–0.09, and realized hit rate is 0.000 across all of them. The estimator is not providing usable ranking information at this horizon.
- The 2:1 target geometry is the largest single lever (−15.12 bps vs −10.93 bps for 1:1 at 15m), but even 1:1 stays negative at every measured horizon.
- Costs are the *least* likely culprit: median gross is already negative.

### On the "LightGBM AUC ≈ 0.637" evidence

That AUC was measured for a **coarse direction classifier on a different feature/target framing**. It does not describe the barrier-conditional path estimator currently feeding the economic gate. The two must be explicitly re-linked before the AUC is cited as evidence that tradable information exists. On the evidence collected here, at the current framing, it does not.

## Q10 — Is there a legitimate candidate population?

**No.** At the current configuration:

- 0 of 30 evaluation candidates have positive net expected value.
- The best geometry/horizon cell measured is still −4.34 bps median.
- Realized target-hit rate is 0.000 in every prediction bucket.

Stage 7 A/B remains **blocked**. There is still no baseline population, and manufacturing one by loosening `minimum_net_expected_value` would be exactly the contamination this stage exists to prevent.

## Recommendation (no action taken)

The evidence points at the **quant layer's target and estimator**, not the LLM, the provider, or the cost assumptions. A legitimate next step would be a separately versioned experiment that evaluates a trained probabilistic model on the barrier-conditional target, measured against this same baseline — with a fresh evaluation period. That is out of scope here and was not started.

Explicitly **not** done in this stage: no threshold lowered, no cost changed, no horizon or R:R adopted, no prompt or model touched, no LLM call, no execution.

```text
REAL ORDERS SENT: NO
PAPER ORDERS SENT: NO
```

## Tests

```text
Full suite: 351 passed, 0 failed, 0 skipped
New: tests/test_live_intelligence_quant_economic_diagnostic.py (8)
```

The new tests pin the break-even solver by round-trip (the solver's algebra was wrong on first implementation and the test caught it), the 15 bps cost stack, calibration monotonicity, and the structural-shortfall claim.
