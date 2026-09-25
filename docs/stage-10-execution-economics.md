# Stage 10 — Execution Economics & Geometry Experiment

Research only. `configs/live.json` is **unchanged** (still 15 bps). No LLM. No execution.

```text
data:      data/btcusdt_1m.parquet (2,629,440 bars, 2021-01-01 .. 2025-12-31)
grid:      PRE-REGISTERED — target ATR {0.5,1,2,3,5} x stop ATR {0.5,1,2} x horizon {15,30,60,120,240,480} = 90 cells
cost:      measured live (Binance BTCUSDT, $1k notional) + documented base-tier fees
artifacts: docs/execution-cost-measurement-2026-09-25.json
           docs/geometry-oracle-grid-2026-09-25.json
```

The grid was fixed before any result was inspected and was **not extended** afterwards. Results are reported as a matrix, not a winner.

## Step 1 — Measured execution economics

The configured stack was ~97% assumption. The historical parquet has **no bid/ask**, so `slippage_bps_per_side=2.0` and `latency_bps=1.0` were never measured.

Measured live (40 samples, public endpoints, no credentials):

```text
spread              median 0.0119 bps   p90 0.0119
taker slippage      median 0.0060 bps   p90 0.2210   (book-walked, $1k)
maker slippage      median 0.0060 bps   p90 0.2210
top-level notional  median ~$810k
```

Cost profiles, each carrying provenance and an achievability flag:

| profile | mode | fee RT | slippage | total RT | achievable |
|---|---|---|---|---|---|
| configured_taker_taker | taker/taker | 10.0 | **assumed** (2/side) | 15.00 | yes |
| measured_taker_taker | taker/taker | 10.0 | measured | **11.01** | yes |
| measured_taker_maker | taker/maker | 14.0 | measured | 15.01 | yes |
| measured_maker_maker | maker/maker | 8.0 | measured | 9.01 | **no** — fill probability not modelled |

The `maker/maker` profile is reported but **never used to declare viability**: a resting order's fill probability is not modelled, so calling it achievable would be exactly the kind of assumption this stage exists to remove.

**Correction: the assumed 2 bps/side slippage was ~330× the measured value.** The stack is 11.0 bps, not 15.0.

## Step 2 — Oracle-bound geometry search (90 cells)

`max_achievable_gross = target_bps − p_stop × stop_bps` is the best any forecaster could do at `p_target = 1.0`.

```text
oracle-feasible cells: 42 / 90
```

At the measured 11.01 bps cost, wide-target geometries clear the oracle bound:

| geometry | target | p_stop | max gross | vs cost |
|---|---|---|---|---|
| 5:0.5@15m | 36.5 bps | 0.820 | 33.49 | +22.49 |
| 5:1@15m | 36.5 | 0.689 | 31.46 | +20.46 |
| 3:0.5@15m | 21.9 | 0.800 | 18.97 | +7.97 |

**A large feasible region exists that the old 15 bps cost had completely masked.** This is a genuine correction to Stage 9's impossibility result, which was conditional on an unmeasured cost.

## Step 3 — Does the model actually profit in that region?

Oracle-viable ≠ tradeable. The Stage 9 model was run on 7 oracle-feasible cells, 4 walk-forward folds, at 11.01 bps:

| geometry | AUC | median net | eligible/fold (2022/23/24/25) | predicted net when eligible |
|---|---|---|---|---|
| 2:0.5@15m | 0.5477 | −10.70 | 2377 / 13 / 0 / 0 | +5.16 |
| 3:0.5@15m | 0.5764 | −10.89 | 1505 / 0 / 0 / 0 | +6.70 |
| 3:1@15m | 0.5708 | −11.39 | 1903 / 0 / 0 / 0 | +6.84 |
| 3:1@60m | 0.5251 | −10.64 | 22442 / 213 / 0 / 26 | +6.24 |
| 3:2@15m | 0.5851 | −11.48 | 5965 / 0 / 0 / 0 | +7.24 |
| 5:0.5@15m | 0.6415 | −11.71 | 785 / 0 / 0 / 0 | +8.31 |
| 5:0.5@60m | 0.5686 | −10.61 | 14424 / 31 / 0 / 8 | +7.74 |

Median net stays negative everywhere. Eligible candidates are a vanishing fraction that **collapses to zero out of sample**.

### The +5 to +8 bps "net when eligible" is an illusion

That column is computed from *predicted* probabilities, and it is positive in every cell — which looked like the breakthrough. It is not. Taking the only cell that produced candidates in the final fold (3:1@60m, 26 eligible in 2025) and asking what actually happened:

```text
2025 eligible: 26 of 525,600
realized among those 26: TP=1  SL=20  TIMEOUT=5
realized hit rate: 0.038   (95% CI [-0.035, 0.112])
crude realized gross: −51.87 bps   vs 11.01 bps cost
```

**The model was confidently wrong.** 26 candidates cannot distinguish edge from luck (a stable rate needs 200+), and the point estimate is strongly negative. The positive "net when eligible" was a property of the model's confidence, not of the market.

## Q5 — Verdict

**FAIL-ECON** — but for a materially different reason than Stages 8–9 stated.

| Stage | Claimed blocker | Actual |
|---|---|---|
| 8 | costs destroy a ~0 bps opportunity | **partly wrong** — real cost is 11 bps, not 15 |
| 9 | barrier geometry is *mathematically impossible* | **conditional on the wrong cost**; 42/90 cells are oracle-viable at 11 bps |
| 10 | — | geometry is *possible*; the **model** cannot find enough of it |

So the bottleneck moved once more, and earlier reports were wrong in a way that would have been expensive to act on:

1. The cost stack was over-stated by 4 bps because slippage was assumed rather than measured.
2. Under the correct cost, a large geometry region survives the oracle bound.
3. But no trained model produces a stable, profitable candidate stream inside it. Discrimination is real (AUC 0.53–0.64) yet far too weak: the best cell yields 0.005% eligible out of sample, and those candidates lost money when actually resolved.

**The `NO EDGE` conclusion survives — but for a better-evidenced reason than before.** It is not "no feasible geometry exists"; it is "a feasible geometry exists, and our current signal cannot find it."

## Three scorecards, kept separate as planned

```text
discrimination   PASS (modest)  AUC 0.53-0.64 OOS, beats trailing baseline
calibration      PASS           predicted tracks actual
economic value   FAIL           median net negative; eligible -> 0 OOS; realized −51.87 bps
```

## Remaining work — unchanged boundaries

- Continuous-return regressor scorecard: **not run.** The barrier result already returns a decisive FAIL, and running it would be searching for a survivor after the fact.
- No cost, threshold, horizon, or R:R was tuned against a fold. The grid is as pre-registered.
- `configs/live.json` untouched at 15 bps.
- Stage 11 A/B remains **closed**.

## Recommendation

Do not open Stage 11. The next question is not "which geometry" — it is **"can any signal reach the discrimination this problem requires"** (AUC ≥ ~0.65 sustained, producing a stable multi-hundred-trade stream). That is a quant-research question, and it should be attacked as a fresh, versioned experiment on a fresh evaluation period, not by loosening thresholds here.

Ablating which barrier is a fair follow-up, but it must be pre-registered before the run, and it is not part of this stage.

```text
REAL ORDERS SENT: NO
PAPER ORDERS SENT: NO
```

## Tests

```text
Full suite: 358 passed (before the numpy fast path)
New this stage: tests/test_execution_economics.py (6)
Extended:      tests/test_live_barrier_parity.py — numpy path pinned to the polars
               reference AND to the live path.py loop (12 total, all pass)
```

The numpy fast path exists only for speed: it reproduces every committed Stage 9 fold within 0.001 AUC on a dual-core laptop (0.5292/0.5450/0.5321/0.5349 vs 0.5259/0.5461/0.5327/0.5354), at 115×–786× the speed. Its three bugs (tie-break inversion, window off-by-one, SHORT-side comparison) were each caught by the parity test rather than by inspection.
