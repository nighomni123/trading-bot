# Stage 9 — Quant Model Re-link & Barrier Prediction Experiment

Research only. Cost stack, policy thresholds, risk, and execution are the frozen Stage 8 values. No execution.

```text
data:    data/btcusdt_1m.parquet (2,629,440 one-minute bars, 2021-01-01 .. 2025-12-31)
geometry: 2:1 ATR-derived barriers @ 15m (the live system geometry), LONG
features:  BASE_PLUS_DERIVED_FEATURE_SET (23 features, reused from state/features.py)
models:   B0 trailing-frequency (live estimator) · B1 LightGBM binary · B2 LightGBM 3-class
folds:    expanding window, 2022 / 2023 / 2024 / 2025 out-of-sample
artifacts: docs/stage9-walkforward-2026-09-25.json
```

## Step 0 — Label parity (the gate on everything else)

The live gate consumes `quant/path.py`, whose semantics differ from the research labeler in reference price and window. `labels/live_barrier.py` provides a vectorized labeler with **proven parity**: a test compares it against the live loop over 584 overlapping samples across both sides and two horizons, asserting identical outcome, target, and stop.

The parity test caught two real bugs during development (a wrong `p_timeout` dependence and a stop/target ordering error), which is precisely why it was written before any training.

## Q1 — Can the barrier event be predicted?

Yes, the event is frequent. Base rates at the live geometry:

```text
TARGET_FIRST  31.7%
STOP_FIRST    62.6%
TIMEOUT        5.7%
```

**This is the finding that reframes Stage 8.** Stage 8 reported `p_target ≈ 0.023`. The true rate is **0.317 — roughly 14× higher**. The discrepancy is not model error; it is an artifact of how the live estimator samples. Reproduced exactly:

```text
live estimator, fixed 0.4%/0.2% barriers, 500 samples:  TARGET 1.6%  STOP 17.4%  TIMEOUT 81.0%
per-row ATR barriers, full history:                      TARGET 31.7% STOP 62.6%  TIMEOUT 5.7%
```

`build_completed_path_samples` builds each historical sample's barriers from **that row's own ATR**, while the live `analyze_path` query matches samples against a **single fixed** `target_fraction`/`stop_fraction` identity. The fixed 0.4% target is simply not the ATR-scaled target the system actually trades, so it matches a far rarer event. The Stage 8 "p_target ≈ 0.02" was measuring a different barrier than the one the economic gate acts on.

## Q2 — Does LightGBM beat the trailing estimator?

Walk-forward, four out-of-sample folds, 525k rows each:

| fold | base rate | B0 trailing AUC | B1 LGBM AUC | B1 PR-AUC | B2 3-class AUC |
|---|---|---|---|---|---|
| 2022 | 0.309 | 0.5245 | 0.5259 | 0.3224 | 0.5292 |
| 2023 | 0.324 | 0.5271 | 0.5461 | 0.3590 | 0.5450 |
| 2024 | 0.322 | 0.5181 | 0.5327 | 0.3453 | 0.5321 |
| 2025 | 0.332 | 0.5183 | 0.5354 | 0.3611 | 0.5350 |

**LightGBM does beat the trailing baseline, consistently and out of sample** — by roughly +0.01 to +0.02 AUC, largest in the middle folds. Models are well calibrated (predicted 0.332 vs actual 0.309–0.332 across folds).

So there *is* modest, real predictive information at the barrier level. This is a more positive result than Stage 8 implied, and it corrects the earlier framing.

## Q3 — Does the information survive the economic gate?

**No.** Under the frozen 15.01 bps cost stack:

| fold | median gross | median net | eligible | required p_target | model p_target |
|---|---|---|---|---|---|
| 2022 | +0.22 bps | −14.78 bps | 1255 / 525600 (0.2%) | 1.22 | 0.333 |
| 2023 | +0.01 bps | −15.00 bps | 3 / 525600 (0.0%) | 1.94 | 0.318 |
| 2024 | −0.28 bps | −15.28 bps | 0 | 1.42 | 0.301 |
| 2025 | +0.08 bps | −14.93 bps | 0 | 1.77 | 0.324 |

Note `required_p_target > 1.0`. That is a **probability**, and it exceeds one — which is arithmetically impossible.

## Why it is impossible — not marginal

Maximum achievable gross occurs at `p_target = 1.0, p_timeout = 0`:

```text
max_gross = target_bps − p_stop × stop_bps
```

Measured across every geometry:

| geometry | target | stop | p_stop | max gross | vs 15.01 bps cost | verdict |
|---|---|---|---|---|---|---|
| 2:1 @ 15m | 9.5 bps | 4.8 | 0.625 | 6.56 | −8.45 | **IMPOSSIBLE** |
| 1:1 @ 15m | 4.8 bps | 4.8 | 0.500 | 2.38 | −12.63 | **IMPOSSIBLE** |
| 1:1 @ 60m | 4.8 bps | 4.8 | 0.503 | 2.37 | −12.64 | **IMPOSSIBLE** |
| 1:1 @ 240m | 4.8 bps | 4.8 | 0.503 | 2.37 | −12.64 | **IMPOSSIBLE** |
| 3:1 @ 15m | 14.3 bps | 4.8 | 0.671 | 11.10 | −3.91 | **IMPOSSIBLE** |

**No probability vector can pass the economic gate at any measured geometry.** The barrier distances are ~5–14 bps while round-trip cost is 15 bps. A perfect oracle would lose money.

This is a stronger result than "the model has no edge." It says the *strategy geometry itself* is unprofitable before any question of prediction quality arises.

## Q4 — Walk-forward integrity

Expanding window, chronological, no random split, four full out-of-sample folds. 2025 was run once after the design was fixed. Seeds fixed (`random_state=7`). No threshold, cost, or geometry was tuned against any fold. All four folds used identical frozen economics.

## Q5 — Verdict

**FAIL-ECON.**

- Prediction is real but small: LightGBM beats the trailing baseline by +0.01–0.02 AUC out of sample, consistently, and is well calibrated.
- That information cannot be monetized: the barrier geometry admits no positive-expectancy trade at a 15 bps cost, and `required_p_target > 1` in every fold.
- The binding constraint is the **trading geometry versus transaction costs**, not model quality.

### Correction to Stage 8

Stage 8 concluded the estimator was "non-predictive" (realized hit rate 0.000 in every bucket). That was an artifact of the fixed-fraction barrier mismatch, not a property of the market. Under correct ATR-scaled labels the event occurs 31.7% of the time and LightGBM predicts it slightly better than baseline. Stage 8's *conclusion that costs dominate* survives and is strengthened; its *claim about the estimator* does not.

## Recommendation

Do not open Stage 10. A better model cannot fix an unprofitable geometry.

The open question is now purely economic/structural:

```text
required: max_gross > 15 bps
actual:   max_gross = 2.4 .. 11.1 bps
```

Candidate directions, none of which were pursued here:

1. **Lower round-trip cost** — 15 bps is an assumption, not a measurement. Fee tier, maker rebates, and better execution could plausibly halve it. This must be established from the venue's actual schedule, not assumed away.
2. **Wider barriers relative to cost** — a horizon long enough that the target distance exceeds 15 bps with a favourable hit rate. The 240m/1:1 cell still fails, so this needs a real search over horizon × R:R, which Stage 8 correctly declined to do silently.
3. **Fewer, larger, lower-turnover decisions** — cost is per-trade; the same edge at lower frequency is the same edge, so this only helps if it raises per-trade gross rather than reducing count.

Any of these is a **new versioned experiment with a fresh evaluation period**, not a parameter tweak to this one.

```text
REAL ORDERS SENT: NO
PAPER ORDERS SENT: NO
```

## Tests

```text
Full suite: 358 passed, 0 failed, 0 skipped
New: tests/test_live_barrier_parity.py (7) — label parity vs the live estimator
```
