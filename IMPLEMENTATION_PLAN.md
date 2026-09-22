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

## P1 — Data ✅ DONE
`data/fetch.py`: Binance klines + funding + OI → BAR_COLUMNS, asof point-in-time merge,
warn-and-continue, idempotent `merge_gap`. Dataset: 2,629,440 rows 1m BTCUSDT perp
[2021-01-01, 2026-01-01), contiguous, 0 dupes (sha256:3025fc12…, see EXP-001).
Validated: OHLC sane, no negative volume, funding/OI fully enriched (0 nulls);
2,365 zero-OI rows (missing quotes recorded as 0) → `oi_change_1d` nulled by the
feature guard, never inf.

## P2 — State ✅ DONE
16 backward-only features + `build_state_dict`. Enough for the first experiment — no new
feature families until an edge is shown. Ceiling: trend_score saturates (see code `ponytail:`).

## P3 — Labels ✅ DONE (EXP-001 PASS)
`labels/engine.py`: `future_return_15m`, `future_max_return_15m`, `future_min_return_15m`
→ `y_up_15`, `y_dn_15`; threshold 0.0014 frozen (cost-derived). Last-horizon rows null.
Gate passed on real data: symmetric up/dn, median ~0, no degenerate skew.

## P4 — Quant ✅ GATE PASS (EXP-002; spec: `docs/evaluation-protocol.md`)
Baselines A–E scored OOS on 2024 (train 2021–2023, test [2025, 2026) frozen, purge/embargo
15 bars at every boundary): A prior auc 0.500/logloss 0.539, B momentum 0.488, C trend
0.482, D logistic 0.613/0.533, **E LightGBM 0.637/0.517, ECE 0.01**. Verdict: ML adds
information (dumb rules rank worse than coin-flip; always-long loses after costs).
Models in `models/`. Caveat: valid is 2024 only — cost survival is P7's gate, not P4's.

## P5 — Jev + Policy 🟡 Mock complete / position-aware pending
JevClient protocol, MockJev/RandomJev, 5 MVP yesno questions, threshold policy with audit
`reasons`. Cost edge-check wired to `configs/costs.json` (derives round-trip when the key
is absent; activates on `expected_return_15`). `exit` thresholds stay unused here —
position context (`PositionState`: side, quantity, entry_price, unrealized/realized PnL,
time_in_position) arrives in P7, where Jev answers ENTER/HOLD/REDUCE/EXIT/REVERSE.
TypeSafe real client swaps in behind JevClient without touching policy/risk; Laya stays
deferred until P4 justifies it.
Known gaps for P7 (observed, not tuned — threshold choice is P7 arm B's job):
- No `p_dn_15` producer exists (quant predicts up only) → defaults 0.0 → ENTER_SHORT
  safely never fires. A down-model is a research decision, not polish.
- MVP thresholds are near-unreachable: p_up ≥ 0.60 hits 0.04% of bars (model is
  calibrated, p99.9 = 0.57) and mock-Jev vetoes 99.6% of those (trade_ok blends
  against strong trends) → 5 ENTER_LONG in 5y. P7 arm-B starting grid (5y fires):
  0.50→446, 0.40→19,918, 0.35→72,134, 0.30→189,950.

## P6 — Risk ✅ DONE
Deterministic kernel, absolute authority. Every `RiskConfig` key is enforced (dead
`min_depth_usd`/`max_open_positions` keys removed — re-add with wiring when P7/P8
provide book data / PositionState). Ceiling: halt state in-memory (sqlite only if
live needs it).

## P7 — Event Simulator 🟡 Loop + arms complete / GATE FAIL on valid (EXP-003)
- P7.1–P7.4 done: `backtest/simulator.py` (BAR→STATE→QUANT→JEV→POLICY→RISK→ORDER→FILL→
  POSITION→PNL), next-open execution, fees/slippage/funding, full-exit accounting,
  JSONL logs (header versions + per-bar records, replay-checked), `scripts/run_ablation.py`.
- Ablation on valid 2024 (test frozen): A random −14.3%, B thr0.40 −6.3%, C ≡ B,
  D (mock-Jev) −0.8% via exposure cut only (win 48.3 vs 48.9 — no selectivity),
  E flat by design. Sweep: thr0.55 win 55.8% yet −0.35% net (gross ≈ −$0.01/trade
  vs $0.10 costs). Hostile 2–3× deepens linearly.
- **Gate verdict: edge does not survive costs → STOP/ITERATE, no P8.** Next: lower
  turnover, cost-aware sizing/thresholds, or admit the 15m edge is below the fee floor.
  (Spec detail lives in `docs/evaluation-protocol.md`: arms A–E, `Perf(D)−Perf(C)` as
  Jev's incremental contribution, hostile 2–3× gate, EXP filing.)

## P8 — Shadow 🟡 LIVE-READY (P7 gate still FAIL; shadow pipeline validated on live data)
Live feed through the same contracts, zero-real-money paper execution, plus a replay reference
where possible. Monitor: data/feature/decision latency, missing/stale data, model errors,
policy/risk rejections, would-be fills/spread/slippage/PnL, calibration, regime. Gate: 2+
weeks with live behavior matching research, then tiny-live (post-MVP).

### P8.1 — Paper execution engine ✅ SHADOW LIVE-VERIFIED
`execution/__init__.py`: `PaperTrader` class — accepts risk-approved actions, fills at
next-open (buffered, lookahead-free), tracks paper position/PnL/funding, writes replayable
JSONL (seq/entry_hash schema compatible with `verify_log`). Zero real money.

`scripts/paper_trade.py`: live shadow trader loop — Binance 1m bars -> state -> quant(LGBM) ->
Jev(mock) -> policy -> risk -> paper exec -> JSONL. Rolling 3000-bar window for feature
warmup; polls every 15-20s; handles Ctrl+C gracefully.

**Live test (2026-09-22):** 4 bars processed, 0 entries (p_up ~0.13-0.15 vs 0.40 threshold,
trade_ok ~0.71 vs 0.75 gate — consistent with P7 verdict: no cost-surviving edge). Latency
634-1548ms/bar. Event log verified via `verify_log` (hash-verified, correct seq). Pipeline
validates end-to-end on live data; no P8→tiny-live promotion until P7 iterate succeeds.

### P8.1 — Prediction-market data input (sentiment + order book) ⬜ IN PROGRESS
Kalshi (`external-api.kalshi.com/trade-api/v2`) and Polymarket (Gamma catalog + CLOB
`/book` + `/prices-history`) added as non-perp data sources in `data/pm.py`, normalized to
`PM_COLUMNS` (snapshot) and `PM_BAR_COLUMNS` (YES-probability OHLC history). The frontier
model reads aggregated sentiment via `build_pm_sentiment` (mean probability, bid/ask spread,
top-10 depth imbalance, 24h volume, liquidity, open interest) — biased to BTC contracts.
Point-in-time safe (snapshot timestamp = server quote time, history filtered to `[start,end)`);
failures are per-market warn-and-continue; no new deps (`requests` only). Kalshi's external
host is WAF-blocked in the sandbox so only the Polymarket path is live-verified here (unit
tests cover Kalshi via mocked `_get`). Polymarket history fetch: `scripts/fetch_pm.py` →
`data/pm_history.parquet`.


## OSS reuse ledger (license-safe)

- **P4 Quant:** chronological purge/embargo behavior was reviewed in
  `stefan-jansen/machine-learning-for-trading` and PurgedKFold references. The
  splitter in `quant/train.py` is an independent implementation; no source was
  copied. `LightGBM-Quant-Model` (Apache-2.0) was reviewed and rejected because
  its shuffled split leaks forward labels.
- **P6 Risk:** cooldown and equity-mode drawdown behavior were reviewed in
  `freqtrade/freqtrade` (GPL-3.0). Only the protection semantics were reused;
  `risk/kernel.py` contains an independent implementation and does not import
  Freqtrade code.
- **P7 Replay:** append-only ordering and canonical entry hashes were reviewed
  in `sumant1122/agentlog` (MIT) and the `seq`/`entry_hash` event-store contract
  in NautilusTrader (LGPL-3.0). The JSONL writer/verifier is independent and
  copies no GPL/LGPL source. OpenCryptoSignalEngine (MIT) was reviewed for
  canonical JSON/config validation; the project uses its own stdlib serializer.
- Repositories without a detected license were used for conceptual review only;
  no code was reused.

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
P1 Data pipeline          ✅ (2.6M rows 2021–2026, validated)
P2 State engine           ✅
P3 Labels                 ✅ (EXP-001 PASS, threshold 0.0014 frozen)
P4 Quant baseline         ✅ (EXP-002 PASS: LGBM auc 0.637 vs 0.50 prior, 2024 OOS)
P5 Jev + Policy           🟡 Mock complete / position-aware pending
P6 Risk kernel            ✅
P7 Event simulator        🟡 Loop + arms done / GATE FAIL (EXP-003: no cost survival)
P8 Shadow                 🟡 LIVE-VERIFIED (paper trader runs on live data; P7 gate still FAIL)
P9 Frontier strategist    ✅ WorldModel + StrategyGenerator + Overseer + Guardrail + BarFeed
                          (116/116 tests green; EXP-004 next: prove real-Jev selectivity)

RESEARCH GATE
P4 probabilistic edge (2024 OOS) does NOT survive costs at any tested threshold
(gross ≈ −$0.01/trade vs $0.10 costs; best band thr0.55 still fee-negative).
STOP/ITERATE per gates. Test period [2025, 2026) stays frozen.

ARCHITECTURE COVERAGE
Core interface separation (Frontier→Policy→Jev→Risk→Execution): 90% complete.
Missing: live exec engine, shadow trader, full observability — all P8, gated.
Stack deferred (Redis/ClickHouse/NATS/Postgres/Grafana/MLflow/Docker/React P0–P8).
```

## Architecture Coverage vs. Target (`trading-platform-architecture.json`)

### Core interface separation (annotation a2): ✅ 90%
**Frontier → Policy → Jev → Risk → Execution** is fully wired:
- `brain` → `frontier/strategy.py`: `StrategyGenerator` produces `ParameterArtifact` (regime-adaptive quant thresholds, Jev questions/thresholds, policy config)
- `policy` → `policy/engine.py`: `decide(quant, jev_answers, cfg)` → `PolicyProposal` (threshold gates + audit trail)
- `jev` → `jev/client.py`: `JevClient.ask(state, questions)` → `Answer`s feed policy
- `risk` → `risk/kernel.py`: `RiskKernel.evaluate()` → `RiskDecision` (absolute authority, non-overridable)
- The frontier brain consumes "sentient data" (`frontier/world_model.py`: regime + calibration drift + Jev answer distributions + PnL attribution + cost ratio) and produces versioned parameter artifacts tested as EXP-xxx experiments.

### Coverage map
| Target node | Code | Status |
|---|---|---|
| sources | `data/fetch.py`, `data/pm.py` | ✅ BTC perp bars + Polymarket/Kalshi sentiment |
| marketstate | `state/features.py` | ✅ 16 backward-only features + `build_state_dict` |
| quant | `quant/model.py`, `quant/train.py` | ✅ LightGBM + 4 baselines, p_up_15 |
| brain | `frontier/strategy.py`, `world_model.py` | ✅ StrategyGenerator + WorldDigest |
| policy | `policy/engine.py` | ✅ Threshold policy + audit trail |
| jev | `jev/client.py`, `jev/mock.py`, `questions.py` | ✅ JevClient protocol, MockJev, 5 MVP questions |
| risk | `risk/kernel.py` | ✅ Hard kernel — absolute authority |
| exec | `execution/__init__.py`, `scripts/paper_trade.py` | ✅ PaperTrader + live shadow (P8.1) |
| obs | simulator JSONL logs + `experiments/EXP-xxx/` | ✅ Replay-checked logs via `verify_log` |
| store | Parquet data files | 🟡 Parquet only (no Redis/ClickHouse/Postgres/bus) |

### What's missing (the remaining 10%)
1. **Tiny-live order bridge** (post-MVP): send paper fills to a broker/exchange API.
   Blocked on P7/P8 gate (cost-surviving edge required).
2. **Shadow trader + adversarial loop** (annotation a4): `Overseer` is built; shadow trader
   now wired via `scripts/paper_trade.py` but needs tiny-live bridge for full kill-strategy.
3. **Full observability** (`obs`): PnL attribution, calibration tracking, degradation monitoring,
   replay-by-timestamp. Logs are replay-checked but no standalone observability service.
4. **Storage/bus** (`store`): Plan defers Redis/ClickHouse/Postgres/NATS/Redpanda/MLflow/Docker/React
   for P0–P8. Parquet + experiment YAMLs serve as registry.

### New: P9 — Frontier strategist (Track B) ✅ COMPLETE
- `frontier/world_model.py`: `WorldModel` compresses live bars + Jev answers + PnL feedback → `WorldDigest` (regime classification, calibration drift, Jev answer distributions, PnL attribution, cost ratio)
- `frontier/strategy.py`: `StrategyGenerator` consumes `WorldDigest` → `ParameterArtifact` (versioned, experiment-gated parameter sets for quant thresholds, Jev questions/thresholds, policy config)
- `frontier/overseer.py`: `Overseer` adversarial review (regime change, cost erosion, calibration drift, Jev no-info, negative edge → KillSignal)
- `frontier/guardrail.py`: `Guardrail` enforces no-PnL-driven-live-updates; promotion only through experiment pipeline
- `live/feed.py`: `BarFeed` streaming Binance adapter feeding the WorldModel
- `dashboard/core.py` + `scripts/dashboard.py`: Streamlit UI dashboard (Status, Experiments, Frontier, Regime, Backtest)

### Resume notes for next session
- **P8 shadow live-verified**: `PaperTrader` + `scripts/paper_trade.py` built and run on
  live Binance 1m BTCUSDT (4 bars, 0 entries — p_up ~0.13 vs 0.40 threshold, consistent with
  P7 FAIL). Pipeline validates end-to-end; logs in `events/shadow_*.jsonl` (replayable via
  `simulator.verify_log`). No tiny-live until P7 iterate succeeds.
- Next step: EXP-004 — prove real Jev selectivity on a thin recorded slice (A1 client + A2 serializer + A4 record ~20k candidate bars + A5 calibration report)
- Gate stays frozen: test period [2025, 2026) untouched until an iterate shows valid profit
- Stack deferred: no Redis/NATS/ClickHouse/Postgres/Grafana/MLflow/Docker/React until cost-surviving edge demonstrated
