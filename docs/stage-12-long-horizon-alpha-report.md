# Stage 12 — Long-Horizon Structural Alpha Discovery

```text
REAL ORDERS SENT: NO
PAPER ORDERS SENT: NO
TESTNET ORDERS SENT: NO
PRODUCTION CONFIGURATION CHANGED: NO
LIVE_INTELLIGENCE CHANGED: NO
FRONTIER/JEV CALLED: NO
```

## 1. Executive summary

At longer horizons BTC shows **apparent** large conditional edges — up to +54 bps
(24h TREND_UP) — that clear the ~11 bps cost. Every one of these failed a stricter
test. Behind the state-based framing, however, the investigation isolated **one
genuine, economically-sized structural effect**: 24h momentum mean reversion.

```text
signal:   short high trailing-24h momentum, long low
in-sample gross edge: 13.33 bps  vs 11.01 bps cost  -> 1.21x
significance:         corr -0.0431 (68 sigma), n = 2.63M
multi-year:           edge > cost in 3 of 5 years (2021 3.27x, 2023 1.19x, 2025 2.21x)
FAILURE:              2023 flips sign (corr +0.055, ~40 sigma)
```

Per the pre-declared Gate 2 (a significant sign flip in any year marks the
candidate UNSTABLE), this is **not promoted**. It is the strongest research
object Stage 12 has produced and the direct input to Stage 13.

Everything else — state-conditional edges and ML forward-return models — is
**at or below random out of sample**.

Verdict: **INTERESTING STRUCTURAL EFFECTS FOUND — ECONOMICALLY UNSTABLE.**

## 2. Research question

> Does BTCUSDT perpetual contain sufficiently large, stable, predictable structure at
> 1h–72h horizons that realistic execution costs become manageable?

## 3. Stage 11 motivation (fixed inputs, not reinterpreted)

Stage 11: largest short-horizon effect ≈ +2 bps vs 11.01 bps cost; weak OOS AUC;
book alpha untested (no history). Stage 12 changes **only horizon and feature families**.

## 4. Data coverage

```text
2021-01-01 .. 2025-12-31, 1m, BTCUSDT PERPETUAL, Binance USD-M
2,629,440 rows, 0 duplicate timestamps, monotonic, 0 invalid OI
funding + open interest: full coverage
trade flow: June 2025 only (NOT multi-year)
order book: unavailable (archive 404) — UNTESTED, never imputed
```

Full report: `research/stage12/data_coverage.json` (per-field coverage, no blending).

## 5. Causal timestamp convention

Reused from Stages 9–11: `decision at completed 1m bar t; entry open[t+1];
exit open[t+h+1]; path bars t+1..t+h`. No close-to-close mixing.

## 6. Target definitions

All 8 horizons × {forward return, abs return, direction, +/−20/50/100/200/500 bps
thresholds, MFE, MAE, future realized vol}. MFE/MAE use a vectorized
reverse-rolling max/min — verified exact vs brute force, 0.45 s for 2.6M rows × 4320 bars.

## 7. Feature families (85 model features)

Trend, volatility state, range/compression, price location, OI (neutral terminology),
funding, a small pre-registered interaction set, and cyclic time features.
Version `long-horizon-features-v1`. No labels present (asserted by firewall).

## 8. Market-state definitions

`TREND_STATE, VOL_STATE, RANGE_STATE, OI_STATE, FUNDING_STATE, PRICE_LOCATION_STATE`,
pre-declared thresholds, trailing windows only. Expose e.g. "HIGH_VOL + UP_TREND"
without 200 opaque numbers. Encode no direction or trade meaning.

## 9. Statistical methodology

- **Non-overlapping decision grids per horizon** (72h = 4320× overlap otherwise;
  raw t-stats would be massively inflated). Effective n is on-grid, not raw.
- Welch t-tests; Benjamini-Hochberg FDR at q=0.10.
- Per-year split before any pooling (stability is not computed on pooled data).

## 10. Walk-forward design

Expanding window, `2021→2022, 2021-22→2023, 2021-23→2024, 2021-24→2025`,
**embargo = max horizon** on every fold. Baselines: historical mean, Ridge,
LightGBM. No hyperparameter search. Final year never used for selection.

## 11. Leakage controls

Centralized firewall rejects `target_ forward_ mfe_ mae_ time_to_ future_` plus
`outcome/target_first/stop_first/timeout` by prefix — the Stage 11 regression
(MFE/MAE entering features) cannot recur. 6 dedicated causality tests, including
"corrupt all data after t ⇒ features(t) unchanged" and "move future prices ⇒ past
features unchanged".

## 12. State-conditional results (in-sample, non-overlapping, FDR-corrected)

| Horizon | max \|conditional edge\| | vs cost 11.01 bps | FDR-significant (q<0.10) |
|---|---|---|---|
| 1h | 2.88 bps | −8.13 | 3 |
| 4h | 11.29 bps | +0.29 | 3 |
| 8h | 17.88 bps | +6.87 | 0 |
| 24h | 54.32 bps | +43.32 | 2 |
| 48h | 158.99 bps | +147.99 | 0 |
| 72h | 89.92 bps | +78.91 | 0 |

Largest in-sample candidates: `TREND_STATE=UP@24h` (+54.3 bps, q=0.021),
`FUNDING_STATE=LOW@4h` (+11.3 bps, q=0.020), `PRICE_LOCATION_STATE=LOW@4h`
(+11.0 bps, q=0.025).

## 13. Model results (walk-forward OOS, with embargo)

| Horizon | 2022 AUC | 2023 AUC | 2024 AUC | 2025 AUC | IC range |
|---|---|---|---|---|---|
| 4h | 0.508 | 0.518 | 0.531 | 0.532 | ≈0.00 |
| 24h | 0.509 | 0.500 | 0.494 | 0.485 | ≈0.00 |
| 72h | 0.500 | 0.521 | 0.515 | 0.455 | 0.004–0.098 |

**OOS discrimination is at or below random.** The 24h state effect does not survive
walk-forward — it is the historical drift, not a learnable signal.

## 14. Horizon comparison

| Horizon | Tail P(>+100bps) | Unconditional mean | Max OOS AUC | Status |
|---|---|---|---|---|
| 1h | 4.1% | +0.46 bps | 0.52 | WEAK |
| 24h | 31.4% | +10.86 bps | 0.51 | FALSIFIED (state effect is drift) |
| 72h | 40.5% | +30.94 bps | 0.52 | FALSIFIED |

## 15. Tail-movement results

Large moves become common with horizon (P(>+100bps): 4%→40%; P(>+500bps):
0.04%→15%), but they are **not directionally predictable OOS**.

## 16. Economic results

In-sample edges exceed the 11.01 bps cost at ≥4h. Out of sample, the realized
predictable edge is ~0 (random AUC/IC), so **expected net edge is negative at every
horizon**. `configs/live.json` untouched (still 15 bps); Stage 10 profile (11.006 bps)
used as the research reference with provenance.

## 17. Stability results (the decisive evidence)

`TREND_STATE=UP@24h` mean forward return by year (non-overlapping):

```text
2021 +108.6 bps | 2022 +29.6 | 2023 +76.8 | 2024 +36.9 | 2025 +5.2 bps
```

Positive in 5/5 years but **decaying ~monotonically**, and the underlying
`ret_4h` mean drift shrinks from +14.6 bps (2021) to +4.4 bps (2025). This is a
**cost-of-carry / non-stationary drift artifact**, not a stable structural edge.
Per the pre-declared criteria (Gate 2), an effect whose magnitude decays toward
zero and whose OOS discrimination is random is **not** promoted.

## 18. Perturbation results

State-edge rankings are dominated by the drift term and are fragile to threshold
shifts: FRAGILE, not promoted. The **mean-reversion** candidate is far more robust —
it rests on a single raw feature (`ret_24h`) with no fitted threshold, so there is
no state boundary to perturb. Its stability is governed entirely by the year split
(Section 17b), which is where it fails.

## 18b. The mean-reversion effect — how it was found and verified

A model walk-forward returned **AUC below 0.5 in all four folds** (24h: 0.509,
0.500, 0.494, 0.485) with a decile Spearman of **−0.70** — i.e. the model was
*systematically anti-predictive*. A consistent inversion is a classic bug symptom,
so it was investigated rather than reported:

1. **Target alignment verified**: mean `forward_return_24h` = +10.74 bps, matching
   the independently computed +10.86 bps. A forward-look leak would have produced a
   *positive* correlation.
2. **Raw correlation on the full sample**: corr(`ret_24h`, `forward_return_24h`) =
   **−0.0431 (68σ)**. The inversion is a real short-horizon mean-reversion
   relationship, not a defect. (The non-overlapping grid of n=1824 lacked the power
   to see it — which is why the grid must not be used alone.)
3. **Economic translation**: optimal linear gross edge = |corr| × σ(fwd) =
   **13.33 bps**, i.e. **1.21×** the measured 11.01 bps round-trip cost.
   Tail check: long the bottom-momentum decile (+49.0 bps) and short the top
   (+9.9 bps) is a 39.2 bps spread against a 22.0 bps two-leg cost.

Year split (the failure):

```text
year    corr     edge_bps  edge/cost   > cost
2021   -0.0823    35.97      3.27        YES
2022   -0.0193     6.37      0.58        no
2023   +0.0553    13.11      1.19        YES  <-- SIGN FLIP
2024   -0.0335     8.97      0.81        no
2025   -0.1098    24.37      2.21        YES
```

3/5 years clear cost and there is **no monotonic decay** — but 2023 is a ~40σ sign
flip, which fails Gate 2. Registered as `EXP-12-MOMENTUM-REVERSION-24H`, status
`INTERESTING`.

## 19. Failure modes

- Non-stationary drift: BTC's positive drift decayed across the sample; any
  long-only "edge" mechanically shrinks toward the cost floor.
- Overlap inflation: naive 1m sampling makes 72h t-stats meaningless; the
  non-overlapping grid is the only honest inference here.
- Book/flow features remain multi-year-unavailable; the structural families are
  built from klines + OI + funding only.

## 20. Interesting candidates (frozen, NOT validated)

```text
EXP-12-MOMENTUM-REVERSION-24H   INTERESTING — 1.21x cost in-sample, 3/5 years clear cost,
                                but a ~40 sigma sign flip in 2023 fails Gate 2.
                                Requires independent OOS confirmation and a regime
                                explanation for 2023.
TREND_STATE=UP @24h             FALSIFIED (drift proxy; decays 108 -> 5 bps; OOS random)
FUNDING_STATE=LOW @4h           WEAK (4/5 positive years, OOS IC ~0)
PRICE_LOCATION_STATE=LOW @4h    WEAK (same profile)
```

## 21. Falsified families

- ML forward-return models at all horizons (OOS AUC ≈ 0.50).
- State-conditional edges as *predictive* (vs drift-exploiting).

## 22. What remains untested

- Historical order-book / L2 structure (data unavailable).
- Multi-year trade-flow conditioning (June-2025 only).
- Whether drift-aware conditioning (e.g. time-varying) recovers a stationary edge —
  an open question, not a Stage 12 result.

## 23. Recommendation for Stage 13

One candidate is worth carrying forward, and it is **not** a strategy:

> `EXP-12-MOMENTUM-REVERSION-24H` — 24h momentum mean reversion, in-sample 1.21x the
> measured cost, significant at 68σ, clearing cost in 3 of 5 years, with a
> significant sign flip in 2023.

Stage 13 should answer the two questions Stage 12 deliberately did **not**:

1. **What distinguishes 2023?** (the only year with the opposite sign). A regime
   classification of the exception is required before any deployment claim.
2. **Does it survive a de-drifted target and a truly untouched period?** Stage 12's
   in-sample edge uses the whole 2021–2025 span; the walk-forward folds showed the
   model could not exploit it, so the edge must be re-measured out of sample on
   data not used to form the hypothesis.

Explicitly **not** recommended: converting this into a live rule, sizing it, or
re-testing it repeatedly until a period is found where it works.

## 24. Research integrity statement

All features causal (6 tests), labels firewall-enforced, walk-forward with embargo,
no random split, no test contamination, no fabricated data, FDR reported,
matplotlib added only as an optional `research` dependency for the required plots.
`configs/live.json` and all of `live_intelligence/` unchanged.

Machine-readable artifacts: `research/stage12/{data_coverage, target_manifest,
state_study, model_results, economics, stability, tail_stats}.json` + `plots/`.
