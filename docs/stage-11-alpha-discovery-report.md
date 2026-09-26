# Stage 11 — Market Microstructure Alpha Discovery Report

Research only. Research substrate only; the existing barrier strategy is untouched.

```text
REAL ORDERS SENT: NO
PAPER ORDERS SENT: NO
PRODUCTION CONFIGURATION CHANGED: NO
FRONTIER/JEV CALLED: NO
```

## A. Data

### Sources

| Source | Dataset | Status |
|---|---|---|
| `data.binance.vision` | `futures/um/daily/aggTrades/BTCUSDT` (June 2025) | **available, 32,593,669 real trades** |
| `data/btcusdt_1m.parquet` | 1m klines + funding + OI (2021–2025) | available, sliced to June 2025 |
| `data.binance.vision` | bookDepth / bookTicker / metrics | **HTTP 404 — unavailable** (STOP A) |

### Coverage and quality (validation report)

```text
trades: 32,593,669 rows, 2025-06-01 .. 2025-06-30 (30 days), 0 quality issues
bars:   44,639 rows, contiguous 1m, 0 OHLC issues
book:   unpopulated (reported, never imputed)
```

Feature frame: **44,580 rows x 107 columns**, 0 validation issues.

### Honest scope limit

June 2025 is **one month**, so "independent years" stability (a Stage 11 aspiration) could not be tested. Every result here is a single-period discovery signal requiring fresh validation. This is STOP D territory for any strong claim, and it is why nothing is labelled PROMISING.

## B. Features (version `features-v1`)

Causal, vectorized, and leak-audited. Groups:

- **Flow (A)**: buy/sell volume & notional, net aggressive volume, volume/notional/trade imbalance, average/median/large-trade size — 1m/5m/15m/30m windows.
- **CVD (B)**: cumulative volume delta, CVD change 1/5/15m, price_up_cvd_down / price_down_cvd_up divergence.
- **Book (C)**: spread, top-level imbalance, microprice, microprice-mid deviation — **implemented but unpopulated on real history** (no book archive).
- **OI/Funding (Phase 3)**: OI change (1/5/15m, pct), funding level/change/zscore, price×OI quadrants.
- **Klines**: retained OHLCV spine.

**Leakage audit** (`tests/test_microstructure_causality.py`): 11 tests, including "corrupt all bars after t ⇒ feature(t) unchanged" and "no forward label appears in the model feature set."

## C. Events (version `events-v1`)

Eight events fired on real data (pre-registered thresholds, no tuning):

| Event | N |
|---|---|
| EVENT_OI_PRICE_DIVERGENCE | 19,126 |
| EVENT_FLOW_SHOCK | 11,851 |
| EVENT_FLOW_BREAKOUT | 3,679 |
| EVENT_PRICE_SHOCK | 2,955 |
| EVENT_OI_PRICE_CONFIRMATION | 2,294 |
| EVENT_FLOW_REVERSAL | 1,941 |
| EVENT_VOLUME_SHOCK | 1,931 |
| EVENT_OI_SHOCK | 1,923 |

## D. Forward-return behavior (conditional − unconditional, bps, t-stat)

| Event | 5m | 10m | 20m | 30m | 60m | 240m |
|---|---|---|---|---|---|---|
| VOLUME_SHOCK | −0.0 | −0.0 | −0.2 | +0.5 | +0.9 | −1.8 |
| FLOW_SHOCK | −0.0 | −0.1 | −0.1 | −0.3 | −0.7 | −0.1 |
| OI_SHOCK | +0.2 | +0.5 | +0.2 | +0.3 | +0.6 | +0.3 |
| PRICE_SHOCK | +0.2 | −0.1 | −0.6 | **−1.7** | **−3.8** | **−7.6** |
| FLOW_BREAKOUT | −0.1 | −0.2 | −0.6 | −1.0 | **−1.8** | −0.5 |
| FLOW_REVERSAL | **+0.7** | +0.6 | **+1.4** | **+1.6** | **+2.0** | +0.7 |
| OI_PRICE_CONFIRMATION | +0.5 | +0.4 | +0.5 | +1.0 | +0.9 | −1.0 |
| OI_PRICE_DIVERGENCE | +0.1 | −0.1 | −0.0 | −0.1 | −0.2 | −0.3 |

Bold = |t| > 2.58 (99%). Two effects are statistically real:

- **FLOW_REVERSAL**: positive at every horizon, +2.0 bps at 60m (t = 2.66). Aggressive selling into rising price precedes continuation up.
- **PRICE_SHOCK**: strongly negative after 30m (−7.6 bps at 240m, t = −5.1). Volatility spikes mean-revert.

## E. Model results (walk-forward: train Jun 1–15, test Jun 16–30)

Targets kept separate per Phase 22. 66 leakage-free features.

| Horizon | Baseline mean | Linear IC | Logistic AUC | LGBM AUC P(up) | LGBM AUC P(+5bps) | LGBM return IC |
|---|---|---|---|---|---|---|
| 10m | +0.06 bps | −0.01 | 0.521 | 0.515 | 0.573 | 0.011 |
| 30m | +0.18 bps | +0.01 | 0.521 | 0.514 | 0.539 | 0.044 |
| 60m | +0.37 bps | +0.05 | 0.538 | 0.513 | 0.527 | 0.017 |

Out-of-sample discrimination is **weak** (AUC 0.51–0.54, IC ≤ 0.044) and roughly flat across horizons. A 30-minute BTC return is close to a martingale at this frequency.

### STOP E was triggered and resolved

An initial run showed AUC 0.98 / IC 0.96 — implausible, so per §37 STOP E the result was **frozen and investigated**. Cause: the model benchmark joined the target frame to the features and `select_model_features` excluded `forward_return_*` but not the 28 **MFE/MAE/time_to path-label columns**, which literally encode the answer. Fixed by excluding every label-derived column by prefix, and pinned with three regression tests. The corrected numbers above are the honest ones.

## F. Economic results (Stage 10 measured cost, 11.01 bps round trip)

```text
signal/horizon combinations evaluated: 48
economically viable (net > 0):          0
```

The largest gross effect observed is **+2.0 bps** (FLOW_REVERSAL, 60m). At 11.01 bps cost that is **~5.5x below break-even**. Best net is about **−9 bps**. Even the significant mean-reversion effect (PRICE_SHOCK, −7.6 bps) is negative in the *helpful* direction and only ~3.4 bps short of covering cost at 240m — but it is a decay effect, not a tradeable edge at this size.

## G. Stability

June 2025 is a single month, so year-by-year stability could not be evaluated. Regime-conditional breakdowns (volatility / trend / time-of-day) are available in the study artifact and are reported for all regimes, with none selected. This is a scope limit, not a finding of stability.

## H. Failure modes

- **Signal magnitude is the binding constraint.** Direction is weakly predictable; *size* is not, so gross edge is ~0–2 bps while round-trip cost is ~11 bps.
- **No microstructure feature survives costs** at any tested horizon.
- **Book-based features untested** (no history), so the most short-horizon-relevant family remains unmeasured.

## I. Classification (Phase 30 — no winner selected)

| Family | Status | Evidence |
|---|---|---|
| Order-flow imbalance / CVD | **FALSIFIED (economics)** | FLOW_REVERSAL +2.0 bps real (t>2.5) but ~5.5x below cost |
| Volume / OI / price shocks | **FALSIFIED (economics)** | all net-negative after cost; PRICE_SHOCK mean-reverts |
| ML forward-return models | **FALSIFIED (economics)** | OOS AUC 0.51–0.54, IC ≤0.044; no edge over cost |
| Order-book family | **UNTESTED** | no historical book data (STOP A) |

**Nothing is labelled PROMISING.** No family has demonstrated economically meaningful conditional returns out of sample.

## Recommendation

The microstructure substrate is built, validated, and leak-audited — but on this data it finds **no economically viable alpha**. This is a valid Stage 11 outcome, and it locates the bottleneck: at 1m–4m horizons on BTCUSDT perpetuals, round-trip cost (~11 bps) dwarfs the predictable component of returns (≤2 bps). Progress requires either a materially cheaper/faster execution mode (which must be modelled honestly, including fill probability) or a longer horizon where the predictable move exceeds cost. Neither is a Stage 12 parameter tweak; both are new research questions.

---

```text
Implementation: schema/ingest/features/targets/events/study/models/economics/registry/CLI
New tests: 4 files, 27 tests (data, causality, events, registry) — full suite 404 passed
Commits: 5e88d2a, 9142bfe, d89aae0 (+ follow-ups)
```
