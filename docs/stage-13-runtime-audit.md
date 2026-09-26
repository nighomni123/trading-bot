# Stage 13 — Runtime Audit (pre-live shadow hardening)

Status: **audit complete, fixes in progress**
Scope: the code that will run continuously against live public market data in
paper mode. Historical research (Stage 9–12) is explicitly out of scope and is
not modified by this stage.

Audit method: direct read of the runtime modules plus the existing test suite
(`134` live-intelligence tests green at the time of the audit). Every claim
below is anchored to `file:line`.

---

## 1. What is genuinely operational

These paths are implemented, wired into the live CLI, and exercised by tests
that drive the real objects (not just helpers):

| Area | Evidence |
| --- | --- |
| Public Binance/Bybit WebSocket ingestion with generation-based invalidation | `data/venue_adapters.py:100` (`WebSocketRunner`), `:227` (`BinanceLiveState.apply`), `:401` (`BybitLiveState.apply`) |
| REST closed-bar backfill with funding/OI enrichment and incremental merge | `data/venue_adapters.py:718`, `data/fetch.py` |
| Source-aware observation store with event-time ordering, sequence-gap, book-coherence and bar-integrity checks | `data/normalization.py:225` (`DataFabric`) |
| Causality filter — no bar and no tick newer than the decision clock enters the environment | `environment/builder.py:112`, `:158`; `live_intelligence/schemas.py:320` |
| Completed-timeframe aggregation (incomplete buckets are dropped, not relabelled) | `environment/builder.py:127-130` |
| Quant evidence (typed analyzer results + path + economic value) | `live_intelligence/quant/analyzers.py`, `quant/path.py`, `quant/economic.py` |
| Provider boundary with bounded timeout, bounded retries, `Retry-After` handling, categorised failures | `live_intelligence/frontier/client.py:138`, `jev/client.py`, `provider_errors.py` |
| Provider output validation before it can reach policy (id, prompt version/hash, timestamp correlation, authority-field ban) | `frontier/strategist.py:93-102`, `jev/evaluator.py:61-73`, `schemas.py:380-389` |
| Deterministic policy with position-aware HOLD/EXIT and economic gates | `live_intelligence/policy/finalizer.py:68` |
| Deterministic risk kernel, hard limits, no override path from intelligence | `live_intelligence/risk.py:59` |
| Paper executor: next-open fill, explicit fees, funding accrual, state save/restore | `live_intelligence/execution/paper.py` |
| Hash-chained ledger with `Decision → Intent → Fill → Trade` semantic linkage and tamper detection | `ledger/decisions.py:55-101`, `:103-136` |
| Atomic checkpoint (temp + `fsync` + `os.replace` + directory `fsync`) with ledger fallback and hard-fail on cross-experiment restore | `live_intelligence/runner.py:185-233` |
| Crash/restart equivalence tests, including "resume must not re-query the provider" | `tests/test_live_intelligence_checkpoint_recovery.py` |
| Ledger-as-intelligence-cache replay | `live_intelligence/runner.py:240`, `replay/loader.py` |

## 2. What is only unit-tested (correct logic, not yet on the live path)

* `research.memory.frontier_context` is called only from the runner, but the
  runner is only ever executed in tests or by `paper --iterations 1`; no
  continuous process has exercised it. `runner.py:445`.
* `experiment.freeze_experiment()` — the manifest writer — is **never called by
  any CLI path** (`grep freeze_experiment` → only `tests/`). Tested, disconnected.
* `provider_smoke.run_provider_smoke` exists and is correct, but is not a
  prerequisite gate of `paper`; nothing forces it before Arm B/C.
* `quant.economic_diagnostic` / `decision_quality` are Stage-8 style read-only
  tools, not part of the live loop; they must not be wired into it.
* Paper-executor funding accrual (`_accrue_funding`) is exercised only by unit
  tests that advance the clock by 8 h; a live session may never cross a funding
  boundary, so its live correctness is unproven.

## 3. What is disconnected

* **No shadow/demo surface.** There is no `shadow` sub-command, no doctor, no
  dashboard, no heartbeat, no manifest emission, no `metrics.jsonl`/`events.jsonl`.
  `observability.metrics_file` is a single JSON blob rewritten every decision
  (`live_intelligence/metrics.py:31`) — it is not append-only telemetry and
  cannot be tailed during a run.
* **Latency breakdown does not exist.** `Metrics.timed()` is defined but never
  used; there is no `data_fetch_ms / quant_ms / frontier_ms / …` record.
* **No shutdown path.** `run_forever` is a bare `while` + `time.sleep`
  (`runner.py:627`); no SIGINT/SIGTERM handling, no pending-intent cancellation
  on exit, no final state print, no shutdown report.
* **No explicit LIVE_DATA_PAPER vs REPLAY mode.** `ReplayAdapter` and
  `BinanceLivePerpAdapter` both satisfy `MarketDataAdapter`; nothing records
  which one produced a decision.
* **Liquidity provenance is dropped.** `BinanceLiveState.fields()` computes
  `bid_depth_notional`/`ask_depth_notional` into tick metadata
  (`venue_adapters.py:323`), but the environment never reads them —
  `top_level_notional` is rebuilt from a single-level price×size product
  (`environment/builder.py:201`).

## 4. What is unsafe (must be fixed before a continuous run)

| # | Finding | Location | Why it matters |
| --- | --- | --- | --- |
| U1 | The runner **overwrites** `environment.liquidity.estimated_slippage` with the configured cost assumption, so the risk slippage gate can never observe the market. | `runner.py:391-396` | A deliberately configured slippage cap becomes a no-op at runtime; the only remaining protection is the raw configured number. |
| U2 | A pending paper intent has no revalidation path that does **not** depend on a fresh policy/risk decision being computed in the same iteration. | `runner.py:544` | Once the loop is changed to "decide only at a bar boundary", a pending order could otherwise sit unrevalidated between boundaries. |
| U3 | The configured `risk.kill_switch` blocks **risk-reducing exits as well as entries**. | `risk.py:74-77`, `:121-135` | A kill switch that also prevents flattening leaves the paper book stuck open; the emergency policy is undefined. |
| U4 | There is no enforced slippage envelope in the executor. The executor applies `costs.slippage_bps_per_side` unconditionally and never compares it to `risk.maximum_slippage_bps`. | `execution/paper.py:59-65` | A fill can be produced outside the configured envelope. |
| U5 | `PaperFill` carries no provenance (no `experiment_id`, `symbol`, `side`, `price_source`, `bar_timestamp`, `execution_model_version`). | `schemas.py:713` | A fill cannot be audited to "which bar, which venue, which run". |
| U6 | `PaperFill.funding_usd` is hard-coded to `0.0`, and there is no explicit statement of the funding model. | `execution/paper.py:199-204` | A reader cannot tell whether funding was modelled or simply never applied. |
| U7 | Frontier events bypass the minimum call interval entirely, and `frontier.periodic_seconds` is never read. | `runner.py:113-127` | One persistent high-severity event can produce one Frontier call per poll. |
| U8 | The decision id is derived from the wall clock at microsecond precision, so a 15 s poll loop produces 4 logically distinct decisions per minute. | `runner.py:373`, `:417` | Duplicate intelligence, duplicate provider spend, and "waiting for the next bar" is indistinguishable from a new decision. |

## 5. What is incorrectly implemented

* **I1 — the slippage gate is theatre (U1).** `ActiveRiskKernel` already falls
  back to the configured cost when `estimated_slippage is None`
  (`risk.py:109-113`), and there is a test asserting exactly that
  (`test_live_intelligence_safety.py::test_configured_slippage_cannot_bypass_risk_cap`).
  The runner then *overwrites* the field, which makes the measured path dead
  code. Correct behaviour: measure, never overwrite.
* **I2 — tick filtering in the environment is by time only.**
  `environment/builder.py:158` filters ticks on `event_timestamp <=
  decision_time` only. An observation of a different instrument, or a spot
  observation, is currently admissible to the environment builder. The *market
  type* of the primary tick is never asserted. The tests named in the Stage-13
  brief (`test_spot_vs_perpetual_rejection`, `test_primary_source_isolation`)
  do not exist.
* **I3 — bar-gap detection is not scoped.** `DataFabric.ingest_bars`
  (`normalization.py:257`) flags **every** 1-minute discontinuity in the whole
  3000-bar warmup frame as a hard failure, and one missing historical kline
  therefore makes the feed permanently unsafe. Fail-closed is correct, but a
  permanently-unsafe system is not operational. Gaps inside the decision window
  (the max timeframe) must be fatal; older ones must be recorded, not fatal.
* **I4 — no explicit bucket contract.** `TimeframeState` exposes only
  `timestamp` (the bucket *end*). There is no `bucket_start`, `bucket_end` or
  `completed` field, so "this observation is a completed bucket" is an
  invariant of the builder rather than a property of the data.
* **I5 — policy does not require liquidity, only checks it when present.**
  `finalizer.py:122` compares `top_level_notional` only `if ... is not None`.
  An environment with *no* book passes policy and is stopped later by risk.
  Policy must be able to reject "unknown liquidity" itself.
* **I6 — Jev validity is checked at evaluation time only.** `jev/evaluator.py:71`
  rejects an already-expired response, but nothing prevents a *replayed* or
  carried-over `JevEvaluation` from being used after `valid_until`.
* **I7 — the liquidation/mark/book field timestamps are not carried.**
  `BinanceLiveState` tracks a single `event_ms` and drops the book when it is
  stale (`venue_adapters.py:294`), but the resulting tick exposes no
  `book_event_timestamp` / `mark_event_timestamp`, so downstream code cannot
  tell a 50 ms-old mark from a 14 s-old mark.
* **I8 — no latency or provider-call telemetry** is written anywhere, so
  "the AI call froze the loop" is not diagnosable after the fact.

## 6. What must be fixed before the first continuous run

1. Decision boundary = completed 1m bar close; poll ≠ decision. (U8)
2. Frontier cadence: `periodic_seconds` honoured, `min_call_interval_seconds`
   an absolute floor, events deduplicated, hourly cap authoritative. (U7)
3. Kill-switch emergency policy defined and tested: no ordinary entries,
   risk-reducing exits explicitly allowed. (U3)
4. Slippage: measure in the environment, never overwrite; executor rejects a
   fill outside `maximum_slippage_bps` instead of capping it. (U1, U4)
5. Fill provenance fields. (U5)
6. Explicit funding model (`DISABLED` vs `LIVE`) recorded in the manifest. (U6)
7. Tick filtering by instrument + market type in the environment. (I2)
8. Scoped bar-gap severity. (I3)
9. `bucket_start` / `bucket_end` / `completed` on every timeframe. (I4)
10. Policy rejects unknown liquidity and expired Jev. (I5, I6)
11. Field-level timestamps for book/mark. (I7)
12. Graceful shutdown, manifest, append-only telemetry, heartbeat, doctor,
    dashboard — the minimum needed to *observe* an unattended run. (I8, §3)
13. `configs/shadow-demo.json` with every value explicit, plus a frozen
    experiment id per configuration change.

## 7. What may intentionally remain incomplete for the first demo

* Bybit remains optional context only; it must never touch execution price.
* Liquidation / OI / funding / depth streams may be absent — they are then
  `null`/unknown, never zero, and are optional in the doctor.
* No new strategy. Arm A/B/C reuse the registered deterministic baseline; a
  run of 100 `NO_TRADE` decisions is an acceptable, and expected, outcome.
* No web dashboard, no distributed runtime, no database, no worker pool.
* No profitability claim of any kind. This stage is an integration experiment.
* Provider calls remain synchronous and bounded by `timeout_seconds ×
  (1 + max_retries)`; a hard ceiling is enforced per call, but a *threaded*
  provider executor is explicitly deferred.
* Equity is marked from the last trade price, not from a mark/index
  convention. The convention is recorded explicitly in every metrics record
  rather than silently assumed.

## 8. Safety boundary confirmed at audit time

* No authenticated exchange client exists in the package; the only network
  callers are `requests` against public klines/depth/premiumIndex/Bybit public
  endpoints and the OpenAI-compatible provider transport.
* `ExecutionMode` has exactly one member (`PAPER`, `schemas.py:75`);
  `ExecutionState` raises on any other value; `ExecutionIntent` and
  `PaperExecutor` both hard-refuse non-PAPER.
* `configs/live.json` pins `execution_mode: "PAPER"` and
  `instrument: "BTCUSDT_PERP"`, both validated in `LiveSettings`.

Therefore at audit time:

```text
REAL ORDERS SENT: NO
REAL ACCOUNT ACCESSED: NO
REAL POSITIONS ACCESSED: NO
REAL BALANCE ACCESSED: NO
PAPER ORDERS: supported, not yet run against live data
```
