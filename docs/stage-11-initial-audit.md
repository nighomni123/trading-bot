# Stage 11 — Initial Audit (Phase 0)

Audit date: 2026-09-26. Audit commit: `5b6a93b`. Read-only except this document.

## Repository structure

```text
src/jev_trading/
  data/              normalized.py, venue_adapters.py, fetch.py
  state/             features.py            <- build_phase2_features (reuse)
  labels/            engine.py, barriers.py, live_barrier.py
  quant/             model.py, engine.py, train.py, evaluation.py, payoff.py,
                     barrier_experiment.py, execution_economics.py, geometry_search.py
  live_intelligence/ config, runner, policy, risk, execution, frontier, jev, schemas
  ledger/ research/ replay/ risk/ execution/ environment/ backtest/ dashboard/
```

## Reusable components (do not modify)

| Component | Location | Reuse for Stage 11 |
|---|---|---|
| `build_phase2_features` | `state/features.py` | kline feature base, 23 features |
| `atr_fraction` | `labels/live_barrier.py` | causal volatility fraction |
| `build_live_barrier_labels_numpy` | `labels/live_barrier.py` | fast labeler, parity-tested |
| `chronological_economic_split` | `quant/engine.py` | purged walk-forward split |
| `binary_probability_metrics` | `quant/evaluation.py` | AUC/PR-AUC/logloss/ECE |
| `select_non_overlapping` | `quant/payoff.py` | non-overlapping trade selection |
| `measure_book` | `quant/execution_economics.py` | measured cost profile (11.01 bps) |
| `StrategyRegistry` | `research/registry.py` | hypothesis registry pattern |

## Components that must not be modified

`configs/live.json`, `live_intelligence/risk.py`, `live_intelligence/execution/`,
`live_intelligence/policy/`, Frontier/Jev prompts, the live barrier strategy,
and all Stage 8–10 artifacts. Stage 11 is additive research surface only.

## Data audit

### Local

```text
data/btcusdt_1m.parquet   2,629,440 rows  2021-01-01 .. 2025-12-31
  columns: timestamp, open, high, low, close, volume, funding_rate, open_interest
```

Contains **no** trade-level and **no** order-book columns.

### Public archive (`data.binance.vision`) — probed

| Dataset | Status | Size |
|---|---|---|
| futures/um/daily/aggTrades/BTCUSDT | **HTTP 200** | 10.07 MB/day |
| futures/um/monthly/aggTrades/BTCUSDT | **HTTP 200** | 407.77 MB/month |
| futures/um/daily or monthly bookDepth | **HTTP 404** | unavailable |
| futures/um/daily or monthly bookTicker | **HTTP 404** | unavailable |
| futures/um/daily or monthly metrics | **HTTP 404** | unavailable |

Verified content of `BTCUSDT-aggTrades-2025-06-01.zip`:

```text
812,702 rows
columns: agg_trade_id, price, quantity, first_trade_id, last_trade_id,
         transact_time, is_buyer_maker
covers: 2025-06-01T00:00 .. 23:59 UTC
```

`is_buyer_maker == true` means the **buyer** was passive, i.e. an **aggressive seller**.
This gives a true trade-side split, which is sufficient for trade flow and CVD.

### Live REST (probed, for schema compatibility only)

`aggTrades` / `bookTicker` / `openInterest` all return HTTP 200 with the expected keys.
The live path can supply book fields that history cannot, so the normalized schema
must accommodate them even where history is absent.

## Data gaps (STOP A boundaries)

| Spec group | Status | Reason |
|---|---|---|
| A — trade flow (buy/sell volume, imbalance) | **AVAILABLE** | aggTrades give true aggressor side |
| B — CVD and price/CVD divergence | **AVAILABLE** | derived from A |
| C — order book depth/microprice | **UNAVAILABLE historically** | bookDepth/bookTicker 404 for BTCUSDT |
| Phase 3 — OI / funding | **AVAILABLE** | present in the 1m parquet |

Order-book features are implemented in the schema and engine, and are exercised by
synthetic fixtures, but must be reported as **unpopulated** on real history rather
than filled with invented values. This is STOP A handling, not a shortcut.

## Proposed new modules

```text
jev_trading/microstructure/
  __init__.py       versions and exports
  schema.py         normalized schemas + validation (Phase 1)
  ingest.py         archive download + normalization (Phase 1)
  features.py       feature engine (Phase 2, 3)
  targets.py        forward-return + path targets (Phase 5, 17)
  events.py         event detector (Phase 4, 15)
  study.py          deterministic event study (Phase 6)
  models.py         baseline models (Phase 7)
  economics.py      economic scorecard (Phase 25, 26)
  registry.py       alpha hypothesis registry (Phase 28)
  report.py         Stage 11 report (Phase 36)
```

## Dependency graph

```text
ingest -> schema -> features -+
                 \  targets ----+-> study -> models -> economics -> report
                  events -------+
registry (independent, written by study)
```

Features and events must not import `targets`; the separation is what makes the
leakage audit meaningful.

## Reuse-first decisions

- Feature computation stays vectorized (Polars/NumPy). The 2.6M-bar set and daily
  trade files must not be looped per bar.
- Forward targets reuse the repository's existing timing convention
  (decision at bar `t`, entry `open[t+1]`, exit `open[t+h+1]`), matching
  `labels/live_barrier.py` so Stage 11 and Stages 9–10 stay comparable.
- Economic evaluation reuses the Stage 10 measured profile (~11.01 bps) with
  provenance preserved, and never edits `configs/live.json`.
