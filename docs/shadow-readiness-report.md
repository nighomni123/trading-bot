# Shadow-readiness report

Corrective implementation commit: `e9bc0e933e516e1d3e71921d1e8a062bb99d0737`
Environment support commit: `f6afd851c7fa68e1fdf150047124a8cd8ee6c2ed`
Live data source commit: `ba3e64e2d571faa13b268d9cd02588f24b1b39fc`
Live-feed safety fix commit: `716ef53ac4f9a79a3debbb74437faa38f0379b9c`
Provider compatibility commit: `6fe72c8`
Provider capability/smoke commit: `e6a484f`
Provider telemetry/replay commit: `77cb692`
Typed tool-call/benchmark commit: `76fd4bf`

## Executive status

**NOT SHADOW READY**

The paper architecture is materially safer and the deterministic fake-provider path now reaches a paper fill, but the acceptance gate is not complete. The remote archive branch could not be pushed because GitHub credentials are unavailable. The local ignored `.env` contains the supplied base URL, model, and a non-empty key. The minimal typed tool-call interface passes both the 24-case interface benchmark and a serial sustainable benchmark (10/10 valid, zero rate limits, p95 24.8s). Crash recovery is now deterministic: a resumed run reproduces the committed intelligence decision sequence without re-querying the provider. The remaining blocker is that the deterministic baseline selects no candidates on the evaluation slice, so a controlled A/B has no baseline population to compare against.

No live-money execution path was introduced. The active system remains PAPER-only.

## Environment configuration

The repository now loads a project-root `.env` automatically. `.env` is ignored,
`.env.example` is tracked, and the loader gives precedence to already-exported
process variables. The local `.env` contains:

```text
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
FRONTIER_MODEL=inclusionai/ling-3.0-flash-fin:free
JEV_MODEL=inclusionai/ling-3.0-flash-fin:free
OPENROUTER_API_KEY=<present locally; never committed>
```

The selected model is documented on the [OpenRouter model page](https://openrouter.ai/inclusionai/ling-3.0-flash-fin:free) as a text-only, finance-focused reasoning model with a 262,144-token context window, up to 32,768 output tokens, free pricing, and two upstream providers. It supports tools, but explicitly excludes `response_format` and structured outputs. The typed tool-call path produces valid minimal Frontier/Jev decisions. An earlier 429 storm was traced to the previously configured key's exhausted quota, not to a model or interface limitation; after credential rotation the endpoint sustains typed tool calls serially with no throttling.

Capabilities are explicit `ProviderConfig` fields and independent `FRONTIER_*` / `JEV_*` environment overrides. The provider smoke command is `python -m jev_trading.live_intelligence provider-smoke --component both`; it records connectivity, request, parsing, schema status, model identity, and `trading_execution: NOT_INVOKED` without printing credentials or starting execution.

The key is present only in the ignored local `.env` and is not present in Git. The providers remain configured as `disabled` in `configs/live.json` until an operator deliberately changes the provider mode to `openai_compatible`.




## Provider capability and experiment controls

`ProviderConfig` now carries explicit `supports_response_format`, `supports_tool_calling`, `supports_reasoning`, and `supports_vision` flags. Frontier and Jev have independent base URL, model, provider, and capability overrides. `configs/openrouter_profiles.json` records the verified Ling, Nemotron, and `openrouter/free` profiles; the free router is explicitly non-comparable for model-quality experiments.

Provider clients now:

- omit `response_format` when unsupported;
- preserve strict Pydantic validation;
- normalize only safe JSON fences/prose wrappers;
- classify transport, authentication, rate-limit, invalid-request, provider, and validation failures;
- record bounded retry count, HTTP status, request ID, provider, and model without storing credentials;
- expose `provider-smoke` without starting paper execution.

Experiment modes accept the explicit aliases `QUANT_ONLY`, `QUANT_POLICY`, `QUANT_FRONTIER`, and `QUANT_FRONTIER_JEV` in addition to A/B/C. Every `Versions` record now carries provider/model identity, capabilities, temperature, output-token limits, prompt/schema versions, and experiment mode.


## Live market-data verification

The live-feed commit adds public Binance and Bybit adapters only:

- Binance primary WebSocket: aggregate trades, ticker, mark/index, liquidations, and top-of-book depth.
- Bybit secondary WebSocket: ticker, order book, trades, and liquidations.
- REST backfill remains available for closed bars and enrichment.
- `CompositeMarketDataAdapter` keeps Binance as the executable price source; Bybit is secondary/cross-market context only.
- No private WebSocket, authenticated request, order endpoint, or exchange trading method is present.

The one-iteration live smoke connected both venues, recorded healthy source-health entries and cross-market observations, and returned `NO_TRADE` with no fill. The full live-feed test file passed `9` tests. Future-dated exchange events are now rejected rather than clamped into a healthy receive-time observation.

## Architecture verification

The active runtime graph is:

```text
Binance/public or Replay data
  → DataFabric.ingest_bars/ingest/quality
  → build_market_environment
      → completed 1m/5m/15m/1h/4h states
      → detect_events attached to environment
  → QuantRegistry + canonical QuantEvidence
  → Frontier (optional Arm B/C)
  → Jev (Arm C)
  → deterministic PolicyFinalizer
  → ActiveRiskKernel
  → next-open PaperExecutor
  → DecisionLedger
  → structured ResearchMemory
  → read-only Frontier context
```

`ShadowRunner` supports arms:

- `A`: Quant + Policy + Risk
- `B`: Quant + Frontier + Policy + Risk
- `C`: Quant + Frontier + Jev + Policy + Risk

The real AI path remains disabled in the checked-in configuration. Provider-only OpenRouter validation was run; no paper order path or external execution provider was called.

## Frontier verification

| Item | Result |
|---|---|
| Provider abstraction | `FrontierClientFactory` supports disabled, replay, and OpenAI-compatible modes |
| OpenRouter transport | OpenAI-compatible `/chat/completions`; `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `FRONTIER_MODEL` |
| Prompt | Configured `frontier/prompts/strategist_v1.txt`; SHA-256 recorded per decision |
| Context | Environment, current position, Quant evidence, system health, strategy performance/research summary |
| Cadence | `should_call_frontier()` and minimum interval enforced |
| Rate limit | Rolling hourly cap enforced |
| Retry | Bounded retries/backoff; provider failure becomes abstention |
| Validation | Request ID, timestamp, prompt version/hash, structured output |
| Authority | No order, sizing, leverage, or risk override fields/API |
| Runtime smoke | HTTP 200 transport, but model output failed strict `StrategyHypothesis` validation; safe abstention |

## Jev verification

| Item | Result |
|---|---|
| Provider abstraction | `JevClientFactory` supports disabled, replay, and OpenAI-compatible modes |
| OpenRouter transport | OpenAI-compatible `/chat/completions`; `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `JEV_MODEL` |
| Prompt | Configured evaluator prompt; SHA-256 recorded |
| Context | Canonical `QuantEvidence`, environment, candidate, position, Frontier questions |
| Expiry | Request/decision IDs, timestamp, prompt, maximum validity, and expiry validated |
| Thresholds | Target probability, stop probability, entry quality, failure risk, and optional liquidity quality enforced |
| Failure behavior | Provider/validation failure becomes abstention and no trade |
| Authority | Evaluator has no order/size/leverage/risk interface |
| Runtime smoke | Blocked by blank `OPENROUTER_API_KEY` |

## Risk verification

Implemented and behaviorally tested:

- PAPER-only mode
- current data-health and event-time freshness
- configured kill switch
- position, notional, and leverage caps
- capital-at-risk and all-in trade-loss sizing
- daily realized loss
- drawdown
- open positions and correlated exposure checks
- spread and configured slippage cap
- liquidity-notional requirement
- rolling order rate
- cooldown state
- pending-intent expiry and current revalidation
- position/action compatibility
- account/execution/pending/position synchronization

The current public smoke correctly rejected stale event data and produced no fill.

## Execution verification

- Entry and exit use the next completed one-minute open.
- Long and short paths are tested.
- Entry gaps through planned barriers are rejected.
- Entry and exit fees are included in trade P&L.
- Slippage is side-aware and included in diagnostics.
- Visible funding is charged once per configured interval and included in P&L.
- Partial fills are explicitly unsupported; `partial_fill_ratio` was removed rather than left as dead configuration.
- No broker/live-order API is reachable.

## Research, replay, and recovery verification

- Structured observations are written for each decision.
- Frontier receives aggregate read-only research context.
- Hourly/daily/weekly reports are period-filtered and immutable.
- Replay reconstructs environment, Quant evidence, Frontier, Jev, policy, risk, intent, fills, trades, and versions.
- Ledger hash-chain tamper detection passes.
- Fill-to-intent and trade-to-fill semantic links are enforced.
- Runtime checkpoints are atomically written and anchored to the ledger hash.
- Missing or mismatched checkpoints fail closed.

Remaining recovery gap: a checkpoint behind the ledger is not yet incrementally rebuilt; startup rejects it. This is safe but does not yet satisfy the full crash-recovery requirement.

## Ablation verification

The active runner has reproducible `A`, `B`, and `C` arms. A focused test proves:

- A does not call Frontier or Jev.
- B calls Frontier but not Jev.
- C calls Frontier and Jev.
- Every decision records its arm and content-addressed versions.

No profitability interpretation is made.

## Test results

### Full suite

```text
327 passed
0 failed
0 skipped
```

### Fake-provider end-to-end

Passed:

```text
data → environment → events → QuantEvidence → Frontier → Jev
→ Policy → Risk → next-open PaperExecutor → fill → DecisionLedger
→ checkpoint → replay reconstruction
```

Test: `tests/test_live_intelligence_fake_e2e.py`

### Public-data smoke

Command:

```bash
.venv/bin/python -m jev_trading.live_intelligence paper \
  --iterations 1 \
  --config /private/tmp/jev-smoke-config.json \
  --ledger /private/tmp/jev-smoke-ledger.jsonl
```

Result: exit `0`.

Observed:

- Binance klines, funding, OI, depth, mark, and index were fetched.
- Current-day OI archive returned the expected not-yet-published 404.
- Data was correctly marked unsafe because event age exceeded the configured threshold.
- Policy returned `DATA_UNSAFE`.
- Risk returned `REJECTED` for stale/unsafe data.
- No paper fill occurred.
- Canonical events and populated depth/mark/index fields were recorded.

### Replay and research smoke

- Replay returned one validated record and one reconstruction.
- Daily research generation returned one report path.
- Reports are generated from structured ledger/observation data.

### Mutation checks

The following safety mutations were each detected by focused tests after strengthening independent guards:

```text
stale rejection
duplicate rejection
kill switch
slippage cap
pending revalidation
Jev expiry
Jev threshold
Frontier cadence
next-open fill
entry-fee accounting
ledger intent linkage
```

No tested critical mutation survived.

### OpenRouter smoke

**PASS.** The local ignored `.env` has the supplied base URL, model, and a non-empty key. The typed tool-call smoke returns HTTP 200 with schema-valid Frontier and Jev decisions. The 24-case interface benchmark produced 24/24 valid tool-call decisions, zero authority violations, p50 10,345 ms, and p95 18,501 ms. A serial sustainable run (workers=1, 15s minimum interval) produced 10/10 valid tool-call decisions with zero HTTP 429s and zero retries, p50 10,458 ms, p95 24,757 ms, at 3.86 requests/minute. An intermediate 429 storm was traced to the previously configured key's exhausted quota; that episode did produce correctly classified, bounded-retry, fail-closed provider failures with no order path started. No key or model response was printed.

## Tool-call interface benchmark

The typed tool-call interface was benchmarked with 12 deterministic Frontier cases and 12 deterministic Jev cases. The benchmark made no execution calls.

```text
Total cases: 24
Valid decisions: 24
Validation failures: 0
Tool-call successes: 24
Authority violations: 0
Latency p50: 10,345.30 ms
Latency p95: 18,500.52 ms
Trading execution: NOT_INVOKED
```

The result is recorded in `docs/provider-benchmark-2026-09-25.json`; the detailed Stage 1–3 report is `docs/stage-1-3-final-report.md`.

## Repository cleanup status

A local archive branch exists at:

```text
archive/pre-live-intelligence-cleanup-2026-09-25
dcb618a6675941b98db777cb1766eac0bae1ee73
```

The required remote push was attempted and failed with:

```text
fatal: could not read Username for 'https://github.com'
```

Therefore no obsolete documentation or experiment material was deleted. Active-main cleanup is intentionally blocked until the archive branch is remotely verified.

## Final gate checklist

- [x] Pending-order revalidation
- [x] Event-time freshness
- [x] Duplicate rejection
- [x] Bar-gap detection
- [x] Causal timeframe timestamps
- [x] Kill switch
- [x] Daily loss
- [x] Drawdown
- [x] Order rate
- [x] Cooldown
- [x] Slippage cap
- [x] Liquidity contract
- [x] Next-open execution
- [x] Long execution
- [x] Short execution
- [x] Entry-gap rejection
- [x] Complete fee accounting
- [x] Funding semantics
- [x] Explicit partial-fill removal
- [x] Barrier-homogeneous path analysis
- [x] QuantEvidence
- [x] Frontier provider factory
- [x] Frontier prompt/hash/cadence/rate handling
- [x] Jev provider factory
- [x] Jev prompt/hash/expiry/thresholds
- [x] Event attachment
- [x] Strategy registry
- [x] Research memory
- [x] Frontier research context
- [x] Replay reconstruction
- [x] Ablation arms
- [x] Experiment hashes
- [x] Atomic checkpoint/fail-closed startup
- [x] Semantic ledger linkage
- [x] Fake-provider E2E
- [x] Mutation tests
- [x] Full pytest
- [x] Public Binance/Bybit live-feed adapters and paper-only source isolation
- [x] Future-dated exchange events fail closed
- [x] Project-root `.env` loading, precedence, `.env.example`, and secret-ignore tests
- [x] Ledger-suffix checkpoint reconstruction and crash/resume equivalence
- [x] Frontier/Jev typed tool-call interface and 24-case interface benchmark
- [ ] Remote archive branch pushed and verified
- [x] OpenRouter live-configuration smoke (typed tool path, 10/10 at serial rate)
- [ ] First controlled A/B experiment (blocked: no baseline population)

## Exact remaining blockers

1. Provide GitHub push credentials and verify `archive/pre-live-intelligence-cleanup-2026-09-25` remotely before deleting any historical material.
2. The quant baseline selects no candidates. Stage 8 traced it, Stage 9 showed LightGBM beats the trailing estimator out of sample but not economically, and **Stage 10 (`docs/stage-10-execution-economics.md`) corrected two earlier errors**: the configured cost was ~97% assumption (measured taker/taker is **11.01 bps**, not 15.0 — assumed slippage was ~330× the measured value), and under that corrected cost **42 of 90 pre-registered geometry cells are oracle-viable**, contradicting Stage 9's "mathematically impossible" claim which was conditional on the wrong cost. However, no trained model produces a stable profitable stream inside that region: median net stays negative, eligible candidates collapse to 0 out of sample, and the one final-fold cohort that existed (26 candidates) realized **−51.87 bps** (1 TP / 20 SL). Verdict: **FAIL-ECON — the bottleneck is signal strength, not geometry or cost.** Stage 11 A/B stays closed; the next question is whether any signal can reach the required discrimination, as a fresh versioned experiment on a fresh evaluation period.
3. Provider latency is the binding constraint on intelligence cadence: p95 ~24.8s per typed tool call at a serial request rate of ~3.9 requests/minute. Availability is no longer a blocker (10/10 valid tool calls, zero 429s, after credential rotation).

Stages 4–10 are documented in `docs/stage-4-7-report.md`, `docs/stage-8-quant-economic-diagnostic.md`, `docs/stage-9-barrier-model-experiment.md`, and `docs/stage-10-execution-economics.md`. Stage 4 (provider reliability) and Stage 5 (deterministic recovery) **PASS**. Stages 7 and 11 A/B remain **not executed**.

Until those blockers are resolved, the correct status is:

**NOT SHADOW READY**
