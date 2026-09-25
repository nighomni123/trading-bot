# Implementation verification audit — live market-intelligence reset

Audit date: 2026-09-25

Audited repository: `/Users/Mitesh Gada/Documents/Projects/jev-trading`

Audited main: `2e0d2c3dc031b5cdf6462794a7fb7b6d030c3caf`

Mode: read-only verification, except for this required report

## Executive conclusion

The reset is **not a completed, behaviorally verified live market-intelligence system**.

The repository contains real, connected implementations for the typed data/environment contracts, deterministic policy and risk classes, paper execution, a hash-chained decision ledger, and manual research-report generation. It does **not** contain a working operational end-to-end AI trading path:

- the CLI always injects disabled Frontier and Jev clients, even when configuration names another provider;
- the public Binance adapter cannot populate the liquidity field required by risk, so the operational runner cannot approve an entry;
- detected events are not attached to `MarketEnvironment`, and Frontier cadence/rate controls are dead code;
- research memory is not in the runtime loop and never feeds Frontier;
- replay verifies and deserializes a ledger; it does not reconstruct decisions;
- crash recovery, durable pending state, venue partial fills, and paper funding are absent;
- several safety invariants fail in direct probes, including execution of a previously approved pending paper order after current data has become unsafe.

No authenticated/live exchange order path was found. The safety findings below concern **paper-experiment integrity and fail-closed correctness**, not live-money order routing.

## Status vocabulary

This report uses only the requested status values in the matrix:

`COMPLETE`, `PARTIAL`, `MISSING`, `STUB`, `UNVERIFIED`, `INCORRECT`, `BLOCKED`.

# A. Git preservation

## Ref and ancestry evidence

| Item | Result | Evidence |
|---|---|---|
| Active `main` | `2e0d2c3dc031b5cdf6462794a7fb7b6d030c3caf` | `git rev-parse HEAD` |
| `legacy-main-phase2-phase3` | `896908ff8c924a8f2390c281891c49ae9fd81780` | `git rev-parse legacy-main-phase2-phase3` |
| Preservation tag object | `c89bf01bc915d4c355ff23da73ea318e115bdcc3` | annotated tag object from `git tag --format='%(objectname)'` |
| Preservation tag peeled commit | `896908ff8c924a8f2390c281891c49ae9fd81780` | `git rev-parse pre-live-intelligence-reset-2026-09-25^{}` and `git cat-file -t` |
| Remote `origin/main` | `896908ff8c924a8f2390c281891c49ae9fd81780` | `git ls-remote --heads --tags origin` |
| Merge-base, legacy vs main | `896908ff8c924a8f2390c281891c49ae9fd81780` | `git merge-base legacy-main-phase2-phase3 main` |
| Merge-base, tag vs main | `896908ff8c924a8f2390c281891c49ae9fd81780` | `git merge-base pre-live-intelligence-reset-2026-09-25 main` |
| Main divergence | 19 commits ahead, zero behind | `git status --short --branch`; `git rev-list --count origin/main..main` |
| Remote publication | New main, legacy branch, and preservation tag are not published | `git ls-remote --heads --tags origin` shows remote `main` still at the baseline and no tag |

The baseline is an ancestor of main. The recent main reflog contains only commits from `896908f` through `2e0d2c3`; no reset or amend appears in the reset lineage. `git fsck --connectivity-only` exits 0. It reports 104 dangling commits and 17 dangling blobs from older object history, but no connectivity error. Those dangling objects do not change the verified baseline-to-main fast-forward lineage.

`git diff --name-status 896908f..main -- 'docs/phase2-*' 'docs/phase3-*' 'docs/phase7/**' 'experiments/**'` is empty. Equivalent diffs over the frozen legacy source modules are also empty. No historical Phase 2/3 file was deleted or modified by the reset lineage.

The `NO EDGE` result remains intact in the historical documents and structured Phase 2/3 artifacts. The audit did not reinterpret it.

The annotated tag is local-only and unsigned. The baseline commit itself remains durable on remote `main`, even though the named preservation ref is not published.

## Working-tree caveat

At audit start and end, the only tracked working-tree modification was `AGENTS.md`, which was pre-existing and unrelated to this audit. It was not touched. Therefore the implementation report's current-state claim that the working tree is clean is not true in the audited state.

The report's “18 commits ahead” count became 19 when the report commit itself was added. `git diff --check 896908f..main` also exits 2 for trailing whitespace at `docs/implementation-report.md:3`, so the final report's `git diff --check` pass claim does not hold for the final main diff.

# B. Actual runtime architecture

## Authoritative entry point

`src/jev_trading/live_intelligence/cli.py:18-50` is the active CLI. The `paper` branch constructs:

```text
load_settings
  → BinancePerpAdapter
  → DisabledFrontierClient
  → DisabledJevClient
  → ShadowRunner
```

The configured provider name is displayed by `status`, but no factory converts `settings.frontier.provider` or `settings.jev.provider` into `OpenAICompatibleFrontierClient` or `OpenAICompatibleJevClient`. The CLI therefore cannot run the configured real-provider path.

## Actual call graph

```text
cli.main("paper")
  → BinancePerpAdapter.fetch_closed_bars()
      → data/fetch.py fetch_klines + fetch_funding + fetch_open_interest
      → merge_enrichment(backward as-of)
  → BinancePerpAdapter.snapshot()
      → latest closed bar + REST bookTicker
  → DataFabric.ingest() / DataFabric.quality()
  → build_market_environment()
      → aggregate_timeframe() / timeframe_features()
  → detect_events()                    [returns detached local tuple]
  → assess_regime()
  → FrontierStrategist.generate()      [CLI always uses DisabledFrontierClient]
  → QuantRegistry.run_all()            [13 generic analyzers]
  → make_candidate()
  → build_completed_path_samples() / analyze_path()
  → calculate_economic_value()
  → JevEvaluator.evaluate()            [CLI always uses DisabledJevClient]
  → PolicyFinalizer.finalize()
  → ActiveRiskKernel.evaluate()
  → DecisionLedger.append_decision()
  → next iteration: PaperExecutor.execute() for one pending intent
  → DecisionLedger.append_fill() / append_trade()

Separate manual command only:
  ReplayEngine → DecisionLedger.verify/load
  ResearchMemory.generate(ledger, period)
```

There is no runtime call from ledger to `ResearchMemory`, and no call from research memory or recent strategy performance back into Frontier. The required research-memory feedback leg is absent.

## Stage evidence

| Stage | Actual source and symbol | Runtime caller | Finding |
|---|---|---|---|
| Entry | `live_intelligence/cli.py:18` `main`; `:48` runner construction | shell | Active CLI is genuine, but provider config is ignored |
| Data | `data/normalization.py:50` `BinancePerpAdapter`; `:186` `DataFabric` | `runner.py:69-74` | One public REST venue; incomplete health and microstructure |
| Environment | `environment/builder.py:39` `build_market_environment` | `runner.py:75-78` | Canonical Pydantic object is passed downstream |
| Timeframes | `environment/features.py:36` `aggregate_timeframe`; `:63` `timeframe_features` | environment builder | Causal rows, but partial buckets receive future end timestamps |
| Events | `environment/events.py:17` `detect_events` | `runner.py:91` | Called, but result is never inserted into the environment |
| Regime | `environment/regime.py:17` `assess_regime` | `runner.py:92` | Deterministic and used in Frontier payload; cannot return UNKNOWN |
| Frontier | `frontier/strategist.py:29` `FrontierStrategist` | `runner.py:103-111` | Provider boundary exists; CLI is disabled-only |
| Quant | `quant/analyzers.py:109` `QuantRegistry` | `runner.py:112` | All analyzers run; most are shallow evidence adapters |
| Candidate | `policy/finalizer.py:26` `make_candidate` | `runner.py:113` | Frontier horizon, invalidation, target logic, and registry requirements are ignored |
| Path | `quant/path.py:16` builder; `:97` analyzer | `runner.py:119-132` | Causal completed-window construction; unsafe pooling and gap semantics |
| Economics | `quant/economic.py:14` `calculate_economic_value` | `runner.py:132` | Cost-adjusted EV exists; empirical assumptions are weak |
| Jev | `jev/evaluator.py:11` `JevEvaluator` | `runner.py:139` | Validity and basic ENTER probabilities validated; CLI disabled-only |
| Policy | `policy/finalizer.py:46` `PolicyFinalizer` | `runner.py:143` | Deterministic; JEV thresholds and REDUCE behavior incomplete |
| Risk | `live_intelligence/risk.py:27` `ActiveRiskKernel` | `runner.py:144` | Independent class; operational account/execution state is not maintained |
| Paper | `live_intelligence/execution/paper.py:22` `PaperExecutor` | `runner.py:82-89` | Paper-only; later-bar close fill, not next-open; stale pending approval reused |
| Ledger | `ledger/decisions.py:21` `DecisionLedger` | runner and CLI | Hash chain verified; semantic intent linkage incomplete |
| Research | `research/memory.py:13` `ResearchMemory` | manual CLI only | Thin report generator; not runtime-integrated |
| Replay | `replay/loader.py:10` `ReplayEngine` | replay/research CLI | Ledger integrity verification and deserialization only |

## Runtime classification of the one-iteration smoke

The public smoke was **STRUCTURALLY IMPLEMENTED / RUNTIME NOT END-TO-END VERIFIED**. Frontier and Jev were disabled, so no candidate or Jev request existed. Execution was structurally tested in isolation, but no real operational-data paper fill occurred.

# C. Requirement matrix

| Requirement | Status | Evidence | Runtime Verified | Tests | Gap |
|---|---|---|---|---|---|
| Legacy preservation branch | COMPLETE | `legacy-main-phase2-phase3` = `896908f` | yes | Git commands | Local ref only; baseline commit is remote-durable |
| Required preservation tag | COMPLETE | Annotated tag peels to `896908f` | yes | Git commands | Tag is local and unsigned |
| Fast-forward preservation / no reset lineage | COMPLETE | Baseline is merge-base and ancestor; recent reflog is commit-only | yes | Git commands | Cannot prove no rewrite before the preserved baseline |
| Phase 2/3 files and `NO EDGE` unchanged | COMPLETE | Protected-path diff is empty; historical docs/artifacts unchanged | yes | historical suite | None in audited reset lineage |
| Active CLI and paper entry point | COMPLETE | `cli.py:18-50` | yes | no CLI test | CLI has no subprocess/behavior tests |
| Full operational data→research loop | PARTIAL | runner ends at ledger; research is manual | disabled-provider smoke only | runner test asserts NO_TRADE | No runtime research feedback; no operational AI path |
| Source-aware metadata | PARTIAL | `MarketTick` and `DataFabric` | public smoke | schema/data tests | Live sequence absent; source key is not instrument/venue-safe |
| BTCUSDT perp vs BTCUSD spot distinction | PARTIAL | config fixes `BTCUSDT_PERP`; schema has market types | public futures URL | settings test | Builder ignores tick instrument/type; injected spot data can be relabeled perp |
| Secondary-source isolation | PARTIAL | builder selects primary tick; no venue averaging | no second source | none | No operational secondary source; same-source multi-instrument contamination possible |
| Live last trade / mark / index | PARTIAL | snapshot sets last and mark to bar close; index absent | public smoke | none | “last” is not a trade; mark is a close proxy; index missing |
| OHLCV | PARTIAL | 1m bars aggregated to five timeframes | public smoke | environment test | Partial higher-timeframe buckets and gaps are not health-gated |
| Trade count, buy/sell/aggressive volume, intensity | MISSING | `FlowState`; adapter populates volume only | public smoke: null flow fields | none | No live trade stream or taker-side aggregation |
| Order book best bid/ask/mid/spread | PARTIAL | REST `bookTicker`; builder derives mid/spread | public smoke | none | No depth, historical spread, or coherent event-time field set |
| Order-book depth/imbalance/liquidity notional | MISSING | adapter leaves depth null | public smoke: null | risk test manually injects | Operational adapter cannot satisfy risk liquidity gate |
| OI, OI change, funding, basis, liquidations | PARTIAL | funding REST and daily OI archive feed current values | public smoke | historical fetch tests | OI change always null; basis/index absent; liquidations fabricated as zero |
| Data health and source failure | INCORRECT | `DataFabric.quality` uses receive age; duplicates excluded from safety | direct probes | one stale-receive test | Old event with new receive is safe; fresh duplicate is safe; bar gaps missed |
| Unsafe current data produces no trade | INCORRECT | policy/risk reject unsafe current decision, but pending fill occurs first | direct pending-order probe | none | `runner.py:82-84` executes stale approval before current health/risk |
| Point-in-time semantics | PARTIAL | active features use supplied past/current rows; no active negative shift | public smoke + source review | path/timezone tests | 5m–4h states can carry timestamps later than decision; receive/event semantics weak |
| Multi-timeframe causal state and gaps | INCORRECT | `aggregate_timeframe` includes incomplete current bucket | public smoke had future 5m/15m/1h/4h timestamps | keys-only test | No complete-bucket or contiguous-bar validation before environment use |
| Canonical `MarketEnvironment` | PARTIAL | `schemas.py:290`; `builder.py:39` | public smoke | construction test | Events/cross-market empty; flow/liquidity/derivatives materially incomplete |
| Required event detection set | PARTIAL | only 7 branches in `environment/events.py` | public env events empty | one vacuous causality test | 8 event families missing; detected events not assembled into env |
| Regime assessment | PARTIAL | `assess_regime` derives trend/vol/funding labels | public smoke: BULLISH | none | No UNKNOWN/abstain state; simplistic; not consumed by Quant/policy |
| Quant is an analytical toolkit | PARTIAL | 13 registry functions | all 13 ran in smoke | registry count test | Most tools echo fields; no individual behavioral assertions |
| Old predictive model excluded from active runner | COMPLETE | no legacy Quant/model imports under `live_intelligence` | source/call search | n/a | Legacy code remains, but is not active |
| Typed Quant outputs | PARTIAL | `QuantAnalysisResult` schema | public smoke | registry test | Most outputs lack sample/probability/payoff/cost context appropriate to analysis |
| Empirical path target/stop/timeout | INCORRECT | builder/analyzer exist | direct gap probe and replay probe | timestamp-gap test only | Samples pool across changing barriers/horizons; price-gap semantics differ from paper stop; expected duration ignored |
| Cost-adjusted economic engine | PARTIAL | fees, slippage, latency, funding, net EV | policy/economic tests | one arithmetic test | Timeout return fixed at zero; no conditioning; paper PnL diverges from economic costs |
| Frontier real-provider implementation | PARTIAL | OpenAI-compatible class exists | not end-to-end | client object test | CLI never instantiates it; no retries; config timeout/model not propagated |
| Frontier inputs | PARTIAL | environment/position/data quality supplied | disabled smoke | none | No current Quant evidence, performance history, research memory, or component health |
| Frontier structured output | PARTIAL | `StrategyHypothesis`, extra-forbid schema | malformed extra-field probe | weak client-only test | ID/timestamp/prompt correlation not checked; non-abstain fields under-validated |
| Frontier authority boundary | COMPLETE | client has no order API; schema forbids extra authority fields | direct malformed probe | safety tests | No execution method or sizing field reaches policy/risk |
| Frontier cadence and rate control | INCORRECT | `should_call_frontier` exists but is never called | two-iteration probe: 2 calls | none | `_last_frontier_call` and `max_calls_per_hour` do not gate calls |
| Jev real-provider implementation | PARTIAL | OpenAI-compatible class exists | not end-to-end | disabled/client test | CLI disabled-only; no retries; configured prompt file is not used |
| Jev inputs | PARTIAL | env, hypothesis, Quant tuple, candidate, questions, position in env | Replay runner probe | runner test | Economic value is not in Quant tuple; strategy/performance context absent |
| Jev structured outputs and validation | PARTIAL | typed state/confidence/validity; ENTER path probs required | expired/malformed probes | weak schema test | Ratings are untyped dictionaries; target/entry-quality/failure thresholds unused |
| Jev short validity | COMPLETE | request ID, prompt version, maximum window, expiry | direct expired-response probe | mutation showed no test | Implementation rejects expiry; focused suite does not protect it |
| Jev authority boundary | COMPLETE | evaluator/client cannot size or execute | source review | safety tests | None found |
| Position awareness | PARTIAL | side, quantity, entry, P&L, stop, target, strategy fields | exit tests | long-only tests | strategy version omitted on entry; thesis/Jev state not maintained; REDUCE not emitted |
| Deterministic non-LLM policy | COMPLETE | `PolicyFinalizer` is ordinary Python | direct policy probes | policy tests | No LLM call in finalizer |
| Policy action coverage and entry gates | PARTIAL | NO_TRADE, ENTER, HOLD, EXIT implemented | direct threshold probe | exits/policy tests | REDUCE dead; JEV target/entry-quality/failure thresholds ignored |
| Independent risk kernel | COMPLETE | separate `ActiveRiskKernel` class | high-confidence rejection probe | risk tests | Narrow independence is complete; state integration is not |
| Risk override test | COMPLETE | confidence 1.0 + policy ENTER still rejected; fill refused | direct probe | related test | Pending approvals are not re-evaluated, a separate bypass |
| Position/leverage/capital/trade-loss sizing | PARTIAL | deterministic sizing code | direct approval tests | one risk approval test | Maximum loss excludes fees/slippage; leverage exists only as a notional cap |
| Daily loss/drawdown/open/correlation/order/cooldown | INCORRECT | checks read `AccountState`/`ExecutionState`; runner never updates them | 13-approval order-rate probe | no state-flow tests | Kill-switch config, cooldown, order rate, daily PnL, and exposure state are non-operative |
| Spread/slippage/liquidity risk | INCORRECT | spread and missing-liquidity checks exist | 100 bps slippage probe | spread test | Configured execution slippage is not converted to `estimated_slippage`; max can be bypassed |
| Paper long/short/entry/exit/stop/timeout/hold/reduce | PARTIAL | executor and policy branches exist | component tests | long-only active tests | No active short/reduce runner test; policy emits no REDUCE |
| Next-bar paper execution | INCORRECT | fill uses later environment `price.last` | public timing and source review | timestamp-only test | Fills at a later bar close, not next bar open; gap/stop semantics not validated |
| Fees, slippage, P&L | INCORRECT | fill fees accumulated; trade subtracts exit fee only | direct fee reconciliation probe | fill existence test | Entry fee omitted from trade net P&L and realized position |
| Paper-only safety | COMPLETE | `ExecutionMode` has only PAPER; schemas/config/executor reject LIVE | source search + status | safety tests | No authenticated or live order adapter found |
| Partial fills and funding | MISSING | config fields exist; executor hardcodes zero funding | trade probe | none | `partial_fill_ratio` and funding interval unused; no venue simulation |
| Crash recovery / durable pending state | MISSING | position, account, pending intent, path samples are process-local | source review | none | Ledger is durable but no restore state machine or atomic append |
| Structured decision ledger | PARTIAL | decision, fill, trade records | public smoke | ledger test | Position/outcome/counterfactual fields are mostly unused |
| Fill/trade relationship enforcement | PARTIAL | checks decision IDs exist | orphan-intent probe | no semantic test | Fill with nonexistent intent ID is accepted if decision ID exists |
| Hash-chained ledger | COMPLETE | canonical SHA-256 previous-hash chain | public replay + tamper probe | tamper test | No external anchoring; full rewrite can recompute chain |
| Hourly/daily/weekly research memory | PARTIAL | manual period string and exclusive-create report | daily CLI from smoke ledger | test does not generate report | No scheduler/period filtering; reports omit most required content |
| Research immutability / structured source of truth | PARTIAL | ledger canonical; reports use exclusive create | daily CLI | registry test | Ledger links/position outcomes incomplete; no automatic observations |
| Hypothesis registry | PARTIAL | schema statuses and revision files | unit only | one registry test | No initial runtime registry, no transition graph, `evidence` argument unused |
| Strategy registry | PARTIAL | static JSON has required descriptive fields | file read | none | Never loaded/validated; runner accepts arbitrary strategy IDs |
| No automatic self-modification | COMPLETE | no active writes to prompts, thresholds, risk, models, or source | source search | n/a | Research proposals remain manual |
| Experiment version capture | PARTIAL | `Versions` stored per decision | public smoke | ledger schema test | Static labels, wrong generic analyzer versions, no Git/config/prompt hashes; freeze helper dead |
| Required ablation capability | MISSING | no active mode/counterfactual computation | source search | none | `counterfactual_without_jev` always null; legacy ablation is separate |
| Replay | PARTIAL | verifies chain and returns validated records | CLI + tamper | one replay test | No market-data reconstruction or decision re-execution |
| Real provider end-to-end | BLOCKED | no credentials supplied and CLI not wired to provider config | no | none | External credentials plus missing integration work block verification |
| Data-source completeness | MISSING | one public Binance REST adapter | source classification below | legacy mock data tests | No active secondary exchange, WebSocket, trade flow, liquidation, or external-event source |
| CLI status/paper/research/replay | PARTIAL | all commands exist | status/paper/replay/research run | no CLI tests | Default replay/research target absent; paper always disabled providers |
| Full test suite | COMPLETE | 239 passed | yes | full suite | Passing count does not establish integration |
| Active test quality and mutation resistance | PARTIAL | 22 active tests; targeted mutations | mutation runs | 22 focused tests | JEV expiry and Frontier invariant mutations survived all active tests |
| Search for incomplete/dead implementation | PARTIAL | repository-wide search completed | yes | n/a | Many dead controls/config fields and legacy “live” duplicates |
| Canonical structured data contract | PARTIAL | same `MarketEnvironment` Pydantic model crosses layers | public smoke | schema tests | Critical fields absent/detached; economic value omitted from Jev Quant tuple |
| Failure closure across all unsafe states | INCORRECT | most current-decision failures fail closed | direct failure matrix | partial tests | Stale event, pending fill, duplicate, kill switch, slippage, and gap cases fail requirements |

# C.1 Data-source completeness

| Source or feed | Classification | Actually populated | Consumed by environment/Quant | Gap |
|---|---|---|---|---|
| Binance USDT perpetual 1m klines | LIVE | OHLCV | yes | internal gaps and partial fetch failures are not health-signaled |
| Binance funding REST | LIVE | latest point-in-time rate/history used for forward-filled bars | current funding only | no funding history/state in environment; no funding event processing |
| Binance daily OI archive | PARTIAL | delayed 5m OI forward-filled | current OI only | current-day 404 observed; OI change always null |
| Binance bookTicker REST | LIVE | best bid/ask and current spread | price/liquidity | no depth; timestamp marked imprecise; no history |
| Mark price and index | MISSING | none | no | mark is copied from bar close; index null |
| Trade/aggressor stream | MISSING | none | no | no count, buy/sell, aggressive volume, intensity |
| Liquidations | MISSING | none | Quant sees fabricated zeros | no operational source |
| Secondary exchange | MISSING | none | no | schema can represent a role, but no adapter or runtime source |
| External event source | MISSING in active loop | legacy prediction-market adapters exist | no | `data/pm.py` is not called by the active runner |
| WebSocket | MISSING | none | no | REST polling only |
| ReplayAdapter | MOCK | caller-supplied bars/ticks | tests only | not a market source and not used by ledger replay |
| OpenAI-compatible Frontier/Jev | BLOCKED | classes only | not selected by CLI | no credentials and no config-to-client factory |

The operational source cannot currently produce `bid_depth`, `ask_depth`, or `top_level_notional`. The public smoke therefore ended with risk reason `missing_liquidity_data`. Enabling the AI providers alone would not make the CLI's public-data path operational.

# C.2 Event detector verification

| Required event | Implementation | Tested | In `MarketEnvironment` | Runtime result |
|---|---|---|---|---|
| Volatility expansion | simple 5m RV vs 15m RV threshold | no | no | detector can return locally |
| Volatility compression | missing | no | no | absent |
| Range breakout | missing as event; structure uses current-bar open/close only | no | no | absent |
| Range failure | missing | no | no | absent |
| Volume shock | 15m volume z threshold | no | no | detector can return locally |
| Return shock | return vs realized-volatility threshold | no | no | detector can return locally |
| Trend transition | missing | no | no | absent |
| Trend acceleration | missing | no | no | absent |
| OI shock | code exists but `oi_change` is always null | no | no | unreachable operationally |
| Funding extreme | current funding threshold | no | no | detector can return locally |
| Liquidation burst | missing | no | no | absent |
| Liquidity deterioration | missing | no | no | absent |
| Liquidity expansion | missing | no | no | absent |
| Spread expansion | fixed spread threshold | no | no | detector can return locally |
| Order-flow imbalance | code exists but buy/sell volume are null | no | no | unreachable operationally |

`runner.py:91` assigns the detector result to a local variable. It never calls `environment.model_copy(update={"events": events})`, and the environment was already passed nowhere before this point. The public ledger consequently had `events: []`. The Frontier event-trigger method is dead.

# C.3 Quant tool inventory

All 13 tools below are called by `QuantRegistry.run_all()` in the runner. The only focused test asserts that 13 named typed results are returned; it does not test each analyzer's behavior.

| Tool | Function | Input | Actual output | Tests | Runtime caller | Finding |
|---|---|---|---|---|---|---|
| Trend | `trend_analyzer` | timeframe directions | direction map and alignment | registry-only | runner | derived but shallow |
| Volatility | `volatility_analyzer` | timeframe RV/ATR | list and regime | registry-only | runner | no calibrated percentile |
| Market structure | `market_structure_analyzer` | current 15m OHLC/range | evidence dict | registry-only | runner | current candle proxy, not historical structure |
| Breakout | `breakout_analyzer` | 5m return, 15m range | evidence only | registry-only | runner | explicitly no continuation probability |
| Mean reversion | `mean_reversion_analyzer` | range position/VWAP distance | evidence only | registry-only | runner | no setup/conditional evidence |
| Volume/flow | `volume_flow_analyzer` | flow state | volume/buy/sell/imbalance | registry-only | runner | live buy/sell absent |
| Derivatives | `derivatives_analyzer` | derivatives state | mostly null fields | registry-only | runner | OI/funding only |
| Open interest | `open_interest_analyzer` | OI/OI change/relationship | OI snapshot | registry-only | runner | change always null |
| Funding | `funding_analyzer` | funding/change | funding snapshot | registry-only | runner | change always null |
| Liquidation | `liquidation_analyzer` | liquidation dict | fabricated zeros | registry-only | runner | missing data represented as zero |
| Liquidity | `liquidity_analyzer` | spread/depth/imbalance | evidence and limitation | registry-only | runner | operational depth null |
| Path placeholder | `path_analyzer` | env/candidate optional | no empirical evidence | registry-only | runner | remains in tuple even when empirical path is appended |
| Opportunity | `opportunity_analyzer` | optional economics | economic value or null | registry-only | runner | runner never supplies economics to it |
| Empirical path | `analyze_path` | accumulated samples | target/stop/timeout frequencies | gap/tail tests | runner | pools incompatible sample definitions |
| Economic value | `calculate_economic_value` | candidate/probabilities/costs | net EV | arithmetic test | runner | current cost-adjusted formula exists |

A ReplayFrontier/ReplayJev runner probe produced two `path` analyses in one decision: a null-sample placeholder and an empirical result with 285 samples. `opportunity.economic_value` remained null even when `DecisionRecord.economic_value` was populated. `ReplayJevClient` explicitly checks that null opportunity field, so its “ready” branch is unreachable in the actual runner wiring and it always abstains.

# C.4 Risk enforcement verification

| Control | Code present | Operationally enforced | Evidence |
|---|---|---|---|
| PAPER-only mode | yes | yes | enum/config/schema/executor; no live API found |
| Data unsafe / stale | yes | no | receive-age bug; pending execution before current risk |
| Position notional | yes | yes for a single current decision | sizing cap probe |
| Leverage | yes, as notional cap | partial | no independent leverage field/order |
| Capital at risk | yes | partial | excludes fees and slippage from maximum loss |
| Trade loss | yes | partial | reference stop distance only |
| Daily loss | check exists | no | runner never updates `AccountState.daily_realized_pnl_usd` |
| Drawdown | check exists | no | runner account/equity stays static |
| Open positions | check exists | no as account state | paper executor separately prevents replacing an open position |
| Correlated exposure | field/check exists | no | no active state update or correlation implementation |
| Spread | check exists | yes when populated | public spread was populated |
| Maximum slippage | check exists | no | `estimated_slippage` is never populated from configured execution costs |
| Liquidity | check exists | yes, and therefore blocks the public adapter | public smoke `missing_liquidity_data` |
| Order rate | check exists | no | 13 evaluations approved with caller order count fixed at 0 |
| Cooldown | check exists | no | no code sets `ExecutionState.cooldown_until` from config or fills |
| Kill switch | manual `halt()` exists | partial | `account.kill_switch` works if injected; `settings.risk.kill_switch=True` was ignored and entry approved |
| Risk recheck before pending fill | missing | no | unsafe current probe still filled |

# D. Critical Gaps

1. **Pending orders are executed before current health and risk are re-evaluated.** A previously valid `ENTER_LONG` approval filled on the next iteration even though the current environment was `safe_for_trading=False` and current policy was `DATA_UNSAFE`.
2. **Staleness is based on receive time, not market event time.** A 60-second-old event received now was reported as age 0, not stale, and safe for trading. The public smoke had an event about 31.5 seconds old under a 15-second threshold but reported a 4.3-second receive age and `safe_for_trading=true`.
3. **The configured risk kill switch is ignored.** With `risk.kill_switch=True`, a valid entry was approved.
4. **The slippage cap is disconnected from actual execution assumptions.** With risk max slippage 5 bps and configured execution slippage 100 bps, environment estimated slippage null, risk approved and a 100 bps fill occurred.
5. **Operational public data cannot satisfy the liquidity gate.** The CLI can never approve a fill using `BinancePerpAdapter` because it supplies no depth/notional.
6. **The real provider configuration is not wired.** The CLI can display `openai_compatible` in status while still constructing disabled clients in `paper`.
7. **Frontier/Jev cadence and cost controls are dead.** Two immediate runner iterations made two Frontier calls despite a 900-second period and 12-call/hour cap.
8. **Events do not enter the canonical environment.** Detectors are not available to Frontier, Quant, policy, or the ledger environment.
9. **Path evidence is not barrier-homogeneous.** Samples with different target/stop fractions and horizons can be pooled because neither `PathSample` nor the accumulator records barrier identity. The empirical path also handles an adverse entry gap differently from the paper position's absolute stop.
10. **Paper P&L omits the entry fee from trade net and realized P&L.** A direct probe showed both fees charged by the executor but only the exit fee included in `TradeRecord.fees_usd` and `net_pnl_usd`.
11. **“Next-bar” execution is later-bar close execution.** The active paper path is not the repository's normative `open[t+1]` execution.
12. **Research feedback is absent.** Reports are manual, thin, unfiltered by period, and never return to Frontier.
13. **Replay is ledger verification, not decision reconstruction.** No market data or versioned policy/risk replay occurs.
14. **Ablation capability is absent.** No active counterfactual arms exist.
15. **Crash recovery is absent.** A process restart does not restore open positions, pending intents, account state, or pending path samples.
16. **Ledger semantic linkage is incomplete.** A fill with a fabricated `intent_id` is accepted if its `decision_id` exists.

# E. Safety-Critical Findings

No live-order routing bypass was found. The following paper/shadow safety failures are nevertheless critical because they violate the required `unsafe data → NO TRADE` invariant and can corrupt a frozen shadow experiment:

1. **Unsafe pending fill:** current quality unsafe, current policy `DATA_UNSAFE`, yet one paper fill and a LONG position were produced from the prior approval.
2. **Stale-event acceptance:** an event 60 seconds old was marked fresh/safe because receive time was current.
3. **Duplicate-event acceptance:** one fresh duplicate was counted but `safe_for_trading` remained true.
4. **Kill-switch bypass:** configured `risk.kill_switch=True` did not reject an entry.
5. **Slippage-limit bypass:** configured 100 bps execution slippage passed a 5 bps risk maximum and filled.
6. **Weak Jev output accepted by policy:** a structurally valid ENTER evaluation with target probability 0.1, entry quality 0.0, and failure risk 1.0 produced deterministic `ENTER_LONG`; the configured thresholds were not applied.
7. **Price-gap/stop mismatch:** historical path barriers are recomputed around each hypothetical entry while the paper position retains absolute candidate stop/target. An adverse entry gap can therefore have incompatible path and live-position semantics.

The direct “Frontier/Jev confidence 1, policy ENTER, risk REJECT” probe did pass: `PaperExecutor` refused the rejected risk decision. The problem is stale approval reuse and disconnected state, not a direct confidence override in `ActiveRiskKernel.evaluate()`.

# F. False Completeness Check

The following `docs/implementation-report.md` claims are not fully supported by current code/runtime evidence.

| Report claim | Audit finding |
|---|---|
| “working tree is clean” | Current tracked state contains pre-existing `AGENTS.md` modification |
| “18 local commits ahead” | Current state is 19; the report commit made the count self-referential |
| `git diff --check` passed | Final baseline-to-main diff fails on report line 3 trailing whitespace |
| Data fabric checks staleness | It checks receive age; old events received now are considered fresh |
| Data fabric rejects unsafe inconsistent data | Only bid>ask is tracked, schema validation normally prevents that input, and duplicates do not make data unsafe |
| Environment includes events | Runner detects events after building the environment and never attaches them |
| Quant is an integrated analytical toolkit | All tools run, but most are field adapters; path/opportunity wiring is contradictory |
| Frontier receives the required research context | It receives environment only; current Quant, performance, memory, and component health are absent |
| Frontier cost/cadence controls exist | `should_call_frontier` and max-call fields are dead; two immediate iterations made two calls |
| Jev prompt is versioned and used | The stored evaluator prompt file is never loaded; Jev client uses a hardcoded one-line system prompt |
| Policy applies Jev requirements | Target-probability, entry-quality, and failure-rating thresholds are configured but unused |
| Active risk enforces daily loss, drawdown, order rate, cooldown, slippage, kill switch, etc. | Several checks read state the runner never updates; kill switch and configured slippage were directly bypassed |
| Paper execution is next-bar | It fills at a later environment bar's `last`/close, not next-bar open |
| Paper execution tracks P&L correctly | Entry fee is omitted from trade net/realized P&L |
| Ledger refuses fills without corresponding intent | It checks only decision existence; a nonexistent intent ID was accepted |
| ResearchMemory supports postmortem storage | It creates a directory; no postmortem writer exists |
| Research reports contain meaningful full research memory | Generated daily report contained only counts and two safety statements |
| Replay is a replay | It verifies/deserializes; it does not reconstruct or re-execute decisions |
| Active focused tests include CLI integration | No active test imports or invokes the CLI |
| Full architecture integration is complete | Runtime stops at ledger; research feedback and real-provider operational path are absent |

The report is directionally honest about disabled providers, richer-data gaps, crash recovery, and venue partial fills. Those limitations are real, but they are not the only material gaps.

# G. Interruption and Architectural Duplication Findings

## Commit-sequence evidence

The reset began as one 3,242-line commit (`1e3fa4c`) and was followed within roughly one hour by 17 fix/integration commits, including:

- empirical path requirements;
- “complete” integrations;
- causal execution;
- expired Jev handling;
- “next-bar” paper fills;
- risk-approved exits;
- metrics/version persistence;
- liquidity requirements;
- position exits;
- economic persistence;
- research CLI;
- timestamp-gap rejection.

This pattern is consistent with an interrupted implementation that was restarted and patched quickly.

## Concrete incomplete-restart signs

1. `detect_events()` is connected to a runner call, but its result is detached from the environment.
2. `should_call_frontier()` and `_last_frontier_call` exist, but the runner never calls the cadence method.
3. `ReplayJevClient` checks `opportunity.economic_value`, but the runner never supplies economics to the opportunity analyzer.
4. A candidate run contains both a placeholder path result and an empirical path result with the same name.
5. Provider classes and provider configuration exist, but the CLI has no factory and always injects disabled clients.
6. Research memory, registries, experiment freezing, component logging, and latency metrics exist outside the runner.
7. The new paper executor claims funding in its module description but hardcodes trade funding to zero.
8. The report calls the shadow loop integrated while its operational data source cannot satisfy its own liquidity gate.

## Multiple implementations

| Concern | Legacy implementation | Active reset implementation | Authoritative for current CLI |
|---|---|---|---|
| Quant | `src/jev_trading/quant/model.py`, `quant/engine.py` | `live_intelligence/quant/` | active reset |
| Policy | `policy/engine.py` | `live_intelligence/policy/finalizer.py` | active reset |
| Risk | `risk/kernel.py` | `live_intelligence/risk.py` | active reset |
| Jev | `jev/client.py`, `jev/adapter.py`, `jev/mock.py` | `live_intelligence/jev/` | active reset |
| Frontier | `frontier/laya.py`, `router.py`, `strategy.py`, `overseer.py` | `live_intelligence/frontier/` | active reset |
| Execution | `execution/__init__.py` `PaperTrader` | `live_intelligence/execution/paper.py` | active reset |
| Ledger/event log | `events.py` schema-v3 log | `ledger/decisions.py` | active reset |
| Replay | `scripts/replay_laya.py` and event-log verifier | `replay/loader.py` | active reset |
| Live feed | `live/feed.py`, containing an integration TODO | `data/normalization.py` + `runner.py` | active reset |

The old modules are not imported by the active runner, so the old LightGBM predictor is not a hidden centerpiece. However, old modules retain authoritative-sounding names and docstrings. `pyproject.toml` still describes the project as `state -> quant -> mock-jev -> policy -> risk -> paper execution`, and `src/jev_trading/live/feed.py` is still named “Live” while its callback integration is TODO. This duplication makes “which implementation is real?” unnecessarily ambiguous even though the new CLI is deterministically selected.

# H. Runtime Verification

## Exact primary commands and results

| Command | Result |
|---|---|
| `git status --short --branch` | `main...origin/main [ahead 19]`; pre-existing `M AGENTS.md` |
| `git branch -a -vv` | local main at `2e0d2c3`, legacy branch at `896908f`, remote main at `896908f` |
| `git log --oneline --decorate -30` | linear reset sequence from `896908f` to `2e0d2c3` |
| `git show --stat --oneline HEAD` | HEAD adds only `docs/implementation-report.md` |
| `git tag --list` | required annotated tag exists |
| `git remote -v` | origin is `https://github.com/nighomni123/trading-bot.git` |
| `git merge-base legacy-main-phase2-phase3 main` | `896908ff8c924a8f2390c281891c49ae9fd81780` |
| `git diff --name-status 896908f..main -- protected historical paths` | empty |
| `git fsck --connectivity-only` | exit 0; 104 dangling commits, 17 dangling blobs, no connectivity error |
| `git diff --check 896908f..main` | exit 2; trailing whitespace in implementation report line 3 |
| `.venv/bin/python -m jev_trading.live_intelligence status` | exit 0; JSON shows PAPER, LIVE ORDERS DISABLED, disabled providers |
| `.venv/bin/python -m jev_trading.live_intelligence replay research/runtime/ledger/decisions.jsonl` | exit 1; requested default ledger absent (`FileNotFoundError`) |
| `.venv/bin/python -m jev_trading.live_intelligence research daily --ledger research/runtime/ledger/decisions.jsonl --root research` | exit 1; same absent-ledger failure |
| `.venv/bin/python -m jev_trading.live_intelligence paper --iterations 1 --config /tmp/jev-audit-live.json --ledger /tmp/jev-audit-ledger.jsonl` | exit 0; real public Binance REST smoke |
| `.venv/bin/python -m jev_trading.live_intelligence replay /tmp/jev-audit-ledger.jsonl` | exit 0; one decision verified |
| `.venv/bin/python -m jev_trading.live_intelligence research daily --ledger /tmp/jev-audit-ledger.jsonl --root /tmp/jev-audit-research-cli --config /tmp/jev-audit-live.json` | exit 0; generated one 15-line report |
| `.venv/bin/python -m pytest -q` | `239 passed in 219.24s (0:03:39)` |
| `.venv/bin/python -m pytest --collect-only -q tests/test_live_intelligence*.py` | 22 active tests collected |

All audit-created repository runtime artifacts were removed. The public smoke used temporary configuration/ledger/metric paths outside the repository. The only final tracked modification besides this report is the pre-existing `AGENTS.md` edit.

## Public smoke evidence

The smoke successfully downloaded:

- 3,000 one-minute klines for the main environment;
- 5 one-minute klines for the snapshot;
- current funding events;
- OI archives for 2026-09-21 through 2026-09-24;
- the expected 404 for the not-yet-published 2026-09-25 daily OI archive;
- current best bid/ask.

The resulting decision recorded:

- `last=84570.1`, `mark=84570.1`;
- `bid=84634.4`, `ask=84634.5`, derived mid `84634.45`;
- last-to-mid difference `7.609 bps`, with no cross-field coherence check;
- event timestamp `09:24:00Z`, decision timestamp `09:24:31.505982Z`;
- reported quality age `4306 ms`, safe, despite event age about 31.5 seconds and a configured 15-second threshold;
- funding `3.512e-05`, OI `95872.91`, OI change null;
- liquidation long/short both `0.0` despite no liquidation source;
- buy/sell volume, depth, and top-level notional null;
- all 13 Quant names present;
- Frontier abstained because disabled;
- no economic value, no Jev request/evaluation;
- policy `NO_TRADE`;
- risk `REJECTED` for `no_trade` and `missing_liquidity_data`;
- no execution intent.

Timeframe timestamps in that same decision were:

```text
1m  09:24:00Z
5m  09:25:00Z
15m 09:30:00Z
1h  10:00:00Z
4h  12:00:00Z
decision 09:24:31.505982Z
```

The four higher-timeframe timestamps were later than the decision because partial current buckets were assigned their nominal bucket end.

## Non-persistent inline safety probes

The following were executed with `.venv/bin/python - <<'PY'` using temporary paths and no source edits:

| Probe | Result |
|---|---|
| 60-second-old event received now | reported age 0, stale false, safe true |
| fresh duplicate event | duplicate count 1, safe true |
| valid Frontier/Jev/policy entry plus configured kill switch | risk approved, proving config kill-switch bypass |
| 5 bps risk max vs 100 bps configured execution slippage | risk approved and 100 bps fill occurred |
| 13 entry evaluations with caller order count 0 | 13 approved, 0 rejected |
| prior valid approval, next iteration current data unsafe | one fill, LONG position, current policy `DATA_UNSAFE` |
| high-confidence Frontier/Jev, policy ENTER, manual risk halt | risk rejected and paper fill refused |
| low-quality but structurally valid Jev ENTER | policy `ENTER_LONG` despite configured thresholds |
| expired Jev response | evaluator rejected |
| malformed Jev ENTER probabilities | evaluator rejected |
| Frontier extra authority field | strategist rejected |
| all 22 active tests with JEV expiry check disabled | all 22 passed; mutation survived |
| all 22 active tests with Frontier primary-strategy invariant disabled | all 22 passed; mutation survived |
| valid ledger with one field changed but hash unchanged | replay exited 1 with hash mismatch |
| fill with fabricated intent ID and valid decision ID | accepted |
| two fees charged vs trade P&L | trade included only exit fee; net was higher by entry fee |
| two immediate runner iterations | two Frontier calls despite 900-second period and 12/hour cap |
| ReplayFrontier + ReplayJev candidate run | two path outputs; opportunity economics null; ReplayJev abstained |
| adverse entry-gap path sample | path economics and absolute paper stop semantics diverged |

The report does not claim that these inline probes are permanent regression tests; they demonstrate current behavior and test gaps.

# I. Test Verification

## Counts

```text
Full suite: 239 passed, 0 failed, 0 skipped
Active live-intelligence tests: 22 collected
Runtime smoke: 1 decision, 0 fills, 0 trades
```

The report's `239 passed` count is correct. The runtime duration differed from the report but the count did not.

## Behavioral test quality

Stronger active tests exist for:

- paper-only schemas;
- basic environment key presence;
- arithmetic economics;
- policy abstention without economics/Jev;
- risk rejection of explicitly unsafe state;
- long paper entry/exit existence;
- stop/target/timeout policy branches;
- timestamp-gap rejection in path construction;
- hash-chain tamper detection;
- hypothesis file versioning.

Weak or missing active coverage includes:

- CLI subprocess/argument behavior;
- real provider construction or HTTP behavior;
- Frontier request cadence/rate/correlation;
- Frontier malformed semantic fields;
- Jev expiry (mutation survived);
- Jev threshold requirements;
- event detector behavior and attachment;
- regime behavior;
- each Quant analyzer;
- path barrier identity, price gaps, duration, and conditioning;
- operational account/execution/risk state updates;
- kill-switch configuration;
- configured slippage cap;
- pending-order revalidation;
- next-open fill price;
- entry-fee P&L accounting;
- funding/partial fills;
- fill-to-intent semantic linkage;
- actual report period/content;
- registry loading/enforcement;
- experiment freeze/hash integrity;
- ablation and true replay.

The test named `test_research_reports_are_immutable_and_hypotheses_are_versioned` does not call `ResearchMemory.generate()`. The paper “next-bar” test changes only the timestamp and retains the same price, so it does not prove `open[t+1]` semantics. The event test asserts causal timestamps on whatever events happen to be returned; the public environment returned none.

## Mutation audit

A temporary `git archive` checkout was created under `/tmp`, mutated, tested with `PYTHONPATH` pointed at that checkout, and removed. The repository source was not changed.

| Mutated behavior | Focused result | Interpretation |
|---|---|---|
| Remove policy missing-economic guard | test failed with `AttributeError` | detected |
| Disable ledger hash comparison | tamper test did not raise and failed | detected |
| Disable timezone-awareness validator | timezone test did not raise and failed | detected |
| Force DataFabric safe | stale test failed | detected |
| Disable all current risk data-rejection branches | stale-risk test failed | detected, but redundant lower layers mean a stale-only branch mutation alone survived |
| Disable Jev expiry rejection | all 22 active tests passed | not protected |
| Disable Frontier non-abstain primary-strategy invariant | all 22 active tests passed | not protected |

# J. Data Contract and Dead Code Findings

## Data contract

The strongest part of the reset is the typed `MarketEnvironment` contract. The runner passes the same Pydantic environment to Quant, Frontier, Jev, policy, and risk, and serializes only at provider boundaries. It does not replace every layer with unrelated ad hoc dictionaries.

The contract is nevertheless incomplete because:

- `events` is detached;
- cross-market is always empty;
- flow, depth, mark/index, liquidation, and many derivative fields are null or fabricated;
- economic value is not represented in the Quant tuple sent to Jev;
- position and execution state diverge from account/risk state;
- asynchronous last/book/mark values share no field-level timestamp contract.

## Dead or disconnected active code

- `ShadowRunner.should_call_frontier()`;
- Frontier/Jev `max_calls_per_hour` settings;
- `DataFabric` sequence support when operational sources provide no sequence;
- adapter `health()` methods;
- `freeze_experiment()`;
- component logger and latency timer calls;
- research hourly/daily/weekly booleans;
- hypothesis/strategy/postmortem/observation config paths in the runner;
- `partial_fill_ratio` and `funding_interval_hours`;
- position `frontier_thesis` and `jev_state` fields;
- `paper_fill`, `eventual_outcome`, and `counterfactual_without_jev` decision fields;
- `_pending_decision_id` and `_last_sequence`;
- standalone validator modules not used by the runtime;
- opportunity analyzer's economic-value input;
- the “ready” ReplayJev branch.

No active TODO/FIXME/NotImplementedError was found in the new core package. The `pass` occurrences are exception-class bodies and an empty schema subclass, not unfinished core methods. Hidden placeholders are more important: empty `CrossMarketState`, `compression_state="UNKNOWN"`, liquidation zeros, null OI change, and disabled observability.

# K. Recommended Next Implementation Phase

This is a corrective implementation phase, not a redesign. The minimum required fixes before a frozen multi-week run are:

1. **Close unsafe-state and pending-order gaps.** Evaluate current data health and current risk immediately before every paper fill; cancel/reject pending intents on unsafe/stale data. Make duplicate/gap/incoherence conditions unsafe and use market event time, not receive time alone.
2. **Correct timestamp construction.** Only emit closed higher-timeframe buckets, require contiguous bars, and ensure no timeframe timestamp is later than the decision. Reject or quarantine missing-bar histories.
3. **Make the active risk configuration authoritative.** Honor `risk.kill_switch`; maintain account/execution/order/cooldown/daily-PnL state from fills; compare actual configured/estimated slippage and all-in stop loss to risk limits.
4. **Align execution and accounting.** Fill at the actual next bar open, handle entry gaps against absolute stop/target semantics, include entry and exit fees in trade/position/account P&L, and implement or explicitly remove claims for funding/partial fills.
5. **Make path evidence barrier-homogeneous and gap-safe.** Carry target, stop, horizon, and conditioning context with every sample; do not pool incompatible samples; use empirical timeout economics and duration.
6. **Wire the configured providers.** Add the minimal config-to-client factory for disabled/replay/OpenAI-compatible modes, use the stored Jev prompt, enforce cadence/rate/timeout/retry policy, and add a safe integration test with a local fake endpoint.
7. **Make the operational data contract satisfy the gates.** Add a real top-of-book depth/liquidity source and correct event-time mark/index or explicitly keep those gates disabled and documented. Do not call a run operational if its native adapter cannot pass its own risk contract.
8. **Finish the research/experiment plumbing.** Attach events, provide current Quant/economic evidence to Jev, add research feedback as an explicit input, implement period-filtered reports, semantic ledger links, real replay/counterfactual arms, and content hashes for code/config/prompts/models/policy/risk/strategy.

# L. Required Final Answers

## Does today’s paper runner implement the specified architecture?

**Not fully.** The runner has a real connected implementation through data, environment, Quant, deterministic policy, risk, paper execution primitives, and ledger. It is not “only empty scaffolding.” However, the operational public-data path cannot trade, the configured real AI providers are not wired, events/research/replay/ablations are not closed-loop, and material safety/accounting defects remain. The fair characterization is **a partially integrated architecture with substantial non-operative scaffolding and incorrect safety behavior**.

## Five most important missing or insufficiently verified pieces

1. Current-health/risk revalidation of pending paper orders, plus correct event-time freshness and complete-bucket timestamps.
2. Operationally enforced risk state/configuration, especially kill switch, slippage cap, daily loss, order rate, cooldown, and account/PnL updates.
3. A real operational market-data contract that supplies coherent liquidity and the fields the active risk/environment gates require.
4. Configured Frontier/Jev provider wiring with real prompt/cadence/rate/threshold enforcement and a non-secret fake-provider end-to-end test.
5. Correct next-open/gap/funding/fee accounting, followed by real research feedback, barrier-homogeneous replay, ablations, and semantic ledger/version provenance.

## Is it safe to begin the multi-week frozen shadow experiment now?

**No.** Live order routing appears safely disabled, but a multi-week paper experiment would be methodologically unsafe and invalid under the current implementation because stale/unsafe pending orders can fill, configured kill/slippage controls can be bypassed, operational data cannot satisfy liquidity, P&L omits an entry fee, higher-timeframe timestamps can be in the future, and research/replay provenance is incomplete. Fix and behaviorally test the minimum items in Section K before beginning the frozen run.

# OVERALL STATUS

ARCHITECTURE UNSAFE — SAFETY-CRITICAL GAPS
