# Implementation Plan — jev-trading (P0 → P8)

> **Single sentence of truth:** Jev is not being built to make money; Jev is being tested
> to determine whether it adds incremental decision value to an already-valid quantitative
> trading process. If it doesn't, remove Jev from the trading path — that is a successful
> experiment too.

Central engineering principle: the real question is whether the system produces an edge
that survives costs, regime changes and out-of-sample testing — not whether an AI trader
can technically be constructed.

## Research pipeline (backtest and live consume the same decision contracts)

```text
Historical Market Data → Point-in-time State/Features → Label Generator
  → Chronological Split + Purge/Embargo → Quant Model → Policy → Jev → Risk
  → Simulator → Evaluation / Attribution
```

P8 is the live version of exactly this pipeline. No separate "research implementation"
vs "live implementation". Until P4/P7 produce out-of-sample, cost-adjusted results, the
project has demonstrated engineering progress — not trading performance.

## Execution semantics (anti-leakage rule, applies to P7/P8)

- `information timestamp = t`, `decision timestamp = t`, `earliest execution = t + latency`.
- MVP rule: **features from bar t → decision at close[t] → execution at open[t+1]** plus
  configurable slippage. Same-bar `close[t]` fills are forbidden (lookahead).
- See `docs/execution-semantics.md`.

## P0 — Bootstrap ✅ DONE
Repo, contracts (`contracts.py`: BAR_COLUMNS, Action), configs, tests. Gate: pytest green.

## P1 — Data 🟡 Code complete / data pending
`data/fetch.py`: Binance klines + funding + OI → BAR_COLUMNS, asof point-in-time merge,
warn-and-continue, idempotent `merge_gap`. Starter dataset: ~2–3 months 1m BTCUSDT perp,
validated (continuity, duplicates, missing bars, funding/OI alignment, price sanity).

## P2 — State ✅ DONE
16 backward-only features + `build_state_dict`. Enough for the first experiment — no new
feature families until an edge is shown. Ceiling: trend_score saturates (see code `ponytail:`).

## P3 — Labels ⬜ (spec: `docs/label-spec.md`)
Raw future columns first, derived labels second:
`future_return_15m`, `future_max_return_15m`, `future_min_return_15m` → derive `y_up_15`,
`y_dn_15`. Thresholds cost-aware (`fee + slippage + margin`; probe 0.10/0.15/0.25/0.50%,
never optimize the threshold on test). Last-horizon rows null. Gate: label distribution,
class balance, threshold sensitivity on real data.

## P4 — Quant ⬜ (spec: `docs/evaluation-protocol.md`)
Baselines in order — A naïve (class prior), B momentum (`ret_5m` sign), C trend
(EMA20>EMA50), D logistic, E LightGBM only if justified. Question is "does ML add
information?", not "is AUC > 0.5". Chronological splits with **purge/embargo ≥ label
horizon** (15m labels overlap). Gate: OOS log-loss/AUC vs dumb baselines; "no edge" stops
the line — do not proceed to P7.

## P5 — Jev + Policy 🟡 Mock complete / position-aware pending
JevClient protocol, MockJev/RandomJev, 5 MVP yesno questions, threshold policy with audit
`reasons`. `exit` thresholds stay unused here — position context (`PositionState`: side,
quantity, entry_price, unrealized/realized PnL, time_in_position) arrives in P7, where Jev
answers ENTER/HOLD/REDUCE/EXIT/REVERSE. TypeSafe real client swaps in behind JevClient
without touching policy/risk; Laya stays deferred until P4 justifies it.

## P6 — Risk ✅ DONE
Deterministic kernel, absolute authority. Ceiling: halt state in-memory (sqlite only if
live needs it).

## P7 — Event Simulator ⬜ (core MVP phase; spec: `docs/evaluation-protocol.md`)
- P7.1 Event loop: BAR → STATE → QUANT → JEV → POLICY → RISK → ORDER → FILL → POSITION → PNL.
- P7.2 Cost model: fees, spread, slippage, funding, latency (next-bar execution).
- P7.3 Position accounting: entry/exit/partial fills, realized+unrealized PnL, fees, funding.
- P7.4 Replayable JSONL event log per decision (timestamps, state_hash, component versions,
  decision, risk_decision, order, fill, cost, position_after).
- **Ablation arms** (same conditions, the point of the phase): A random control, B quant-prob
  threshold rule, C quant+policy, D quant+mock-Jev+policy, E Jev-disabled. Report
  `Perf(C+D Jev) − Perf(C)` as Jev's incremental contribution (precision, calibration,
  drawdown, cost-adjusted return, regime robustness).
- Hostile-cost gate: survive 2–3× fee multiplier before P8. Every result filed under
  `experiments/EXP-xxx/` per `docs/experiment-protocol.md`.

## P8 — Shadow ⬜ (production equivalence, not just hypothetical PnL)
Live feed through the same contracts, zero-size paper execution, plus a replay reference
where possible. Monitor: data/feature/decision latency, missing/stale data, model errors,
policy/risk rejections, would-be fills/spread/slippage/PnL, calibration, regime. Gate: 2+
weeks with live behavior matching research, then tiny-live (post-MVP).

## Gates

```text
P4: does an edge exist? NO → STOP/RESEARCH; YES → P5/P6 → P7
P7: does edge survive costs? NO → STOP/ITERATE; YES → P8
P8: does live match research? YES → tiny-live [post-MVP]
```

## Infra / scope discipline
Python + Polars + Parquet + JSONL (+ SQLite only where genuinely useful) + pytest. No
Redis/NATS/ClickHouse/Postgres/Grafana/MLflow/Docker/React in P0–P8. No new feature
families (order book, sentiment, on-chain, macro…) until the tiny set shows an edge.

## Status
```text
MVP STATUS
P0 Bootstrap              ✅
P1 Data pipeline          🟡 Code complete / data pending
P2 State engine           ✅
P3 Labels                 ⬜
P4 Quant baseline         ⬜
P5 Jev + Policy           🟡 Mock complete / position-aware pending
P6 Risk kernel            ✅
P7 Event simulator        ⬜
P8 Shadow                 ⬜

RESEARCH GATE
No evidence of trading edge yet.
```
