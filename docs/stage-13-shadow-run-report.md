# Stage 13 — Shadow Run Report

Status: **first live paper demonstration executed against real public data**
Scope: operational integration only. No profitability, alpha, or live-trading
readiness claim is made anywhere in this document.

Every number below was produced by the runs described in §2 and can be
regenerated with `jev shadow inspect` / `jev replay` against the ledgers
recorded in §3.

**Runs executed for this report**

| Run | Arm | Config | Polls | Decisions | Window (UTC) | Outcome |
| --- | --- | --- | --- | --- | --- | --- |
| Smoke | A | `configs/shadow-demo.json` | 44 | 12 | 07:21:42 → 07:32:12 | clean, 0 fills |
| Provider smoke | — | both | — | — | — | Frontier + Jev PASS, execution NOT_INVOKED |
| Verification | C | `configs/shadow-demo-arm-c.json` | 34 | 10 | 07:52:41 → 08:01:06 | clean, 0 fills, 1 provider call |

The verification run recorded **34 polls, 34 healthy primary feeds, 0 feed
interruptions, 10 decisions over 10 distinct completed 1m buckets, 118 market
events, 0 provider failures, 0 unsafe decisions**, a maximum primary-feed age of
191 ms (mean 81 ms), and a verified ledger chain.

---

## 1. Objective

Run the complete Jev pipeline continuously against real-time Binance BTCUSDT
perpetual data, generate genuine paper decisions, execute only simulated
fills, maintain a durable ledger, and expose enough telemetry that the
behaviour can be inspected while it runs — while remaining structurally
incapable of placing an authenticated exchange order.

The experiment asks whether the *system* behaves correctly. It does not ask
whether the strategy makes money.

## 2. Configuration

| Field | Arm A smoke | Arm C verification |
| --- | --- | --- |
| Config | `configs/shadow-demo.json` | `configs/shadow-demo-arm-c.json` |
| Experiment id | `SHADOW-BTCUSDT-DEMO-001` | `SHADOW-BTCUSDT-DEMO-002` |
| Arm | A (Quant + Policy + Risk) | C (Quant + Frontier + Jev + Policy + Risk) |
| Execution mode | PAPER | PAPER |
| Live orders | DISABLED | DISABLED |
| Instrument | BTCUSDT_PERP (Binance USDT-M futures) | same |
| Primary / secondary | binance / bybit | same |
| Poll interval | 15 s | 15 s |
| Frontier cadence | 900 s periodic, 900 s floor, 4/h cap, severity 0.6 | same |
| Jev validity | 60 s, 60/h cap | same |
| Starting capital | $10,000 paper | $10,000 paper |
| Funding model | `DISABLED` (explicit, reported) | `DISABLED` |
| Risk | \$1000 notional, 2x leverage cap, \$50 daily loss, 10% DD, 5 bps slippage cap, 100 USD min top-of-book | same |
| Costs | 5 bps fee/side, 2 bps slippage/side, 1 bps latency | same |

Both configurations are frozen at run start: `research/runtime/.../manifest.json`
records the git commit, `config_hash`, resolved config, prompt hashes, models,
capital and cost assumptions, and is never rewritten for the life of the run.

The two runs use **different experiment ids and separate ledgers** because they
are different configurations (providers disabled vs enabled). They cover
different market windows, so no A/B performance comparison is claimed.

## 3. Market Data

Source: public Binance USDT-M REST (`klines`, `premiumIndex`) and public
WebSocket (`aggTrade`, `ticker`, `markPrice@1s`, `forceOrder`, `depth5@100ms`),
with Bybit public linear WebSocket as optional secondary context.

Pre-flight doctor (`shadow doctor`), run immediately before the Arm C run:

| Check | Result |
| --- | --- |
| execution mode is PAPER | PASS |
| live order capability absent | PASS (`ExecutionMode` has exactly one member) |
| paper executor / policy / risk operational | PASS |
| ledger + checkpoint writable | PASS |
| public Binance 1m data | PASS (3000 bars) |
| public WebSocket connectivity | PASS |
| system clock sanity | PASS (last closed bar within ~1 min of now) |
| 1m bar continuity | PASS (no gaps) |
| primary source health | PASS (sub-second lag) |
| book freshness / depth notional | PASS (spread ~1e-6, top-5 depth notional ~$1.5M) |
| Frontier provider configured and reachable | PASS (openai_compatible, `inclusionai/ling-3.0-flash-fin:free`) |
| Jev provider configured and reachable | PASS (same provider) |
| open interest / funding | PASS (optional) |
| secondary Bybit source | WARN (optional; occasionally tens of seconds behind) |
| **Verdict** | **SHADOW RUN READY** |

`provider-smoke --component both` passed for both components with
`trading_execution: NOT_INVOKED`.

## 4. Runtime Architecture

```
Binance public REST + WS ─┐
Bybit public WS (context) ┴─> DataFabric (event-time, bar integrity, source roles)
                                    │
                            completed-bucket environment
                                    │
             Quant ──> Frontier (cadence-gated) ──> Jev (candidate-gated)
                                    │
                          deterministic policy ──> deterministic risk
                                    │
                          pending paper intent (next-open)
                                    │
                      fill ──> hash-chained ledger + checkpoint
```

The decision boundary is the close of the latest completed 1m bar. A 15-second
poll observes continuously but decides once per minute; Quant, Frontier and Jev
run only on a new boundary.

## 5. AI Provider Usage

- Real provider: OpenRouter, OpenAI-compatible transport, model
  `inclusionai/ling-3.0-flash-fin:free`, configured through
  `configs/shadow-demo-arm-c.json` plus the deployment environment.
- Arm C verification run: **1** Frontier call across 10 decision boundaries
  (the 900 s cadence floor held), **0** Jev calls, because Frontier abstained
  and therefore no structurally eligible candidate existed.
- Observed Frontier latency: **~13.1 s**, inside the configured 30 s bound.
  The call blocks the poll loop for its duration; the loop is not frozen and
  the next poll proceeds normally.
- Provider failures during the run: **0**.
- Standalone provider smoke: connectivity, request, JSON parsing, schema
  validation and request-id correlation all PASS for Frontier and Jev, with
  `trading_execution: NOT_INVOKED`.

## 6. Quant Behavior

Quant executed once per decision boundary on the real environment and produced
a complete evidence payload every time: regime, regime confidence, twelve
typed analyzer results with versions, limitations, and (when a candidate
existed) path probabilities, uncertainty, sample size and economic value.
`test_stage13_*` plus `test_live_intelligence_quant_evidence` assert that the
ledger, the Jev request and the decision share one coherent Quant payload.

## 7. Policy Behavior

Policy remained deterministic and authoritative. In both runs every decision
resolved to `NO_TRADE`, with the documented reasons attached
(`deterministic` economic gates unmet, Jev absent, provider abstention). No
policy decision was ever derived from an unvalidated provider payload: the
Frontier response is parsed, schema-validated, correlated by request id,
timestamp and prompt hash, and authority-checked before policy sees it.

## 8. Risk Behavior

Risk rejected every action for the same reason — there was no approved action
to size. The kill switch, slippage cap, liquidity gate, daily-loss limit,
drawdown limit, order-rate limit, cooldown and open-position limit are all
enforced on live state, refreshed after every fill, and covered by focused
tests. The configured slippage cap now gates on the *measured* half-spread
rather than on the configured cost assumption, so it can no longer be satisfied
by construction.

## 9. Paper Execution

No fill occurred, because no policy action was ever `ENTER_*`. The next-open
contract is therefore demonstrated by test rather than by a live fill:
`test_next_open_execution` proves a fill can only come from the 1m bucket that
opens at the decision boundary, never from the last price, the mid, or a later
bucket; `test_slippage_cap` proves an out-of-envelope fill is rejected rather
than capped; `test_funding` proves funding is charged only across a spanned
funding interval and is `DISABLED` by explicit configuration.

## 10. Ledger Integrity

`jev shadow inspect` and `jev replay` both re-read and re-verify the ledger
from disk: hash chain, semantic links, no duplicate ids. Result for the Arm C
verification ledger: `integrity: VERIFIED`, 10 decisions, 0 fills, 0 trades, 0
duplicate decision ids, 0 unsafe decisions, chain head
`e46436680f1613e0e536a1a4248b68006db3168491f9109dbf728249d5b44602`.

Fills cannot exist without their intent, and trades cannot exist without their
fills — including against fabricated identifiers
(`test_fill_requires_intent`, `test_trade_requires_fill`, `test_ledger_tampering_fails_closed`).

## 11. Crash Recovery

- A restart against the live Arm A ledger restored 12 decisions, a FLAT
  position and $10,000 equity, with no pending order and a verified chain.
- `test_shutdown_checkpoint_and_restart_recovery` covers the automated case:
  a pending intent is cancelled on shutdown, the checkpoint records `pending:
  null`, and the resumed run neither re-queries the provider nor duplicates a
  fill.
- A checkpoint from another experiment is rejected outright; a corrupt
  checkpoint falls back to the committed ledger suffix; a missing checkpoint
  rebuilds from the ledger.

## 12. Provider Failures

Injected and tested rather than waited for: HTTP 429, timeout, malformed
output, missing credential, expired Jev evaluation. In every case the component
abstains, the failure is recorded on the decision, the loop keeps running, and
no position is ever opened. `trading_execution` stays `NOT_INVOKED` for
provider-only paths.

## 13. Latency

| Stage | Mean | Max |
| --- | --- | --- |
| data fetch (bars + observations) | ~544 ms | ~9.9 s (initial 3000-bar warmup) |
| environment construction | ~49 ms | ~195 ms |
| Quant | ~0.35 ms | ~0.8 ms |
| Frontier (real provider) | 13.1 s | 13.1 s |
| policy | ~0.05 ms | ~0.11 ms |
| risk | ~0.07 ms | ~0.14 ms |
| ledger + checkpoint write | ~2.8 ms | ~6.2 ms |

The provider call is the only stage that materially exceeds the poll interval.
It is bounded by `timeout_seconds x (1 + max_retries)`; moving it off the poll
thread is deferred (see §16).

## 14. Paper P&L

```
gross P&L          0.00
fees               0.00
slippage           0.00
funding            0.00  (funding_model = DISABLED)
net P&L            0.00
trade count        0
win rate           n/a
max drawdown       0.00%
```

**DEMONSTRATION SAMPLE — NOT PERFORMANCE VALIDATION.** A zero-trade window is
an acceptable outcome of a correct system and yields no information about edge.

## 15. Observed Failure Modes

| # | Observed | Handling |
| --- | --- | --- |
| F1 | Decision timestamp read before ingestion marked a just-received tick as "future", making the whole feed permanently unsafe | The decision instant is now read after ingestion; the run is HEALTHY |
| F2 | Any retransmitted event (quiet tape, polled REST bootstrap) counted as a duplicate and was fail-closed forever | Retransmissions are deduplicated and counted; only ordering, sequence, coherence and bar gaps are fatal |
| F3 | One missing historical kline in the 3000-bar warmup disabled trading permanently | Bar-gap severity is scoped to the decision window; older holes are counted as `historical_bar_gaps` |
| F4 | A timeframe bucket built from an incomplete history was materialized as complete | Only buckets with all constituent 1m bars are materialized; otherwise the environment fails closed |
| F5 | A slippage estimate was overwritten with the configured cost, making the risk cap unfalsifiable | The measured half-spread is used; the executor rejects out-of-envelope fills |
| F6 | A kill switch also blocked risk-reducing exits, trapping the paper position | Kill switches stop new risk only; exits remain approvable and record the switch |
| F7 | A 15 s poll produced four independent decisions per minute, each with its own provider budget | The decision boundary is the completed 1m bar; committed decisions are replayed |
| F8 | Heartbeat freshness reported a lagging optional venue as if it were the primary feed | Primary and maximum feed ages are reported separately |
| F9 | A ledger path and a checkpoint path could diverge, breaking the single-directory invariant | The ledger is placed beside its checkpoint by default |

## 16. Remaining Risks

1. **Provider calls block the poll thread.** A 13 s Frontier call inside a 15 s
   poll means market state is not refreshed while it runs. The bound is
   enforced; the isolation is not. The fix is a worker thread with a
   handoff, deferred until a real provider shows latency that matters.
2. **Book gaps are not detected on Binance's partial-depth stream.** The
   `depth5@100ms` stream exposes no per-update sequence the adapter can
   validate, so continuity rests on the per-field staleness rule. A proper
   `@depth@100ms` diff stream with `pu`/`U` sequence validation is the upgrade.
3. **Funding is not modelled.** `DISABLED` is explicit and reported; an
   overnight run that crosses 00:00/08:00/16:00 UTC will understate costs.
4. **Arm A and Arm C were not run over the same market window**, so no
   ablation comparison is possible yet.
5. **The demo arm may produce no trades for a long time** without any signal
   failure. That is the correct behaviour, but it means the paper-execution
   path is still only demonstrated by tests.
6. **One process, one thread for decisions.** No supervision, no watchdog, no
   restart-on-crash beyond manual rerun.

## 17. Readiness Status

**READY for a longer frozen shadow run (Layers 7–8).** The first demonstration
met the success criteria:

- Binance feed stayed operational, book, OI and funding present
- completed 1m decisions advanced exactly once per minute
- no duplicate decisions
- higher-timeframe timestamps causal and buckets complete
- Quant ran on the real environment
- Frontier ran when the arm and cadence allowed it
- provider failures fail closed
- policy and risk stayed deterministic and authoritative
- ledger, checkpoint, replay and restart recovery all verified
- shutdown clean, no executable order left behind
- zero real orders, zero authenticated calls

**NOT READY for live capital.** Structural paper-only enforcement is intact, but
one session of ten minutes proves integration, not safety under prolonged
failure, nor any economic property.

## 18. Next Step

Run the frozen Arm C configuration for a multi-hour overnight session with an
external watchdog and a 1% of-notional alerting threshold on the depth
notional, then measure provider call latency distribution and the frequency of
feed interruptions — the two quantities this run could not yet characterise.

---

## Appendix A — Verification block (recorded at the end of this stage)

```text
========================================
STAGE 13 VERIFICATION
========================================

RUNTIME AUDIT: PASS

LIVE DATA:
  Binance: PASS
  Book: PASS
  Timestamp integrity: PASS
  Gap handling: PASS
  Source isolation: PASS

ENVIRONMENT:
  Completed timeframes: PASS
  Causality: PASS
  Events attached: PASS

QUANT:
  Real evidence: PASS
  Economic evidence: PASS

FRONTIER:
  Configured provider: PASS
  Cadence: PASS
  Rate limit: PASS
  Validation: PASS
  Failure abstention: PASS

JEV:
  Configured provider: PASS
  Prompt loaded: PASS
  Expiry: PASS
  Threshold validation: PASS

POLICY:
  Deterministic authority: PASS

RISK:
  Kill switch: PASS
  Slippage cap: PASS
  Liquidity gate: PASS
  Daily loss: PASS
  Drawdown: PASS
  Pending revalidation: PASS

PAPER:
  Next-open fill: PASS
  Fees: PASS
  Funding: PASS
  P&L: PASS

LEDGER:
  Intent linkage: PASS
  Fill linkage: PASS
  Trade linkage: PASS
  Hash chain: PASS
  Checkpoint: PASS

RECOVERY:
  Shutdown: PASS
  Restart: PASS
  Deduplication: PASS

REAL ORDERS SENT: NO
REAL ACCOUNT ACCESSED: NO
REAL BALANCE ACCESSED: NO
REAL POSITION ACCESSED: NO

PAPER RUN COMPLETED: YES

DEMO STATUS: READY

NEXT RESEARCH QUESTION:
Does the paper-execution path behave correctly under a live feed once a
regime actually produces an eligible candidate, and what is the real
distribution of provider latency and feed interruptions over a multi-hour
session?
========================================
```

Test evidence at the time of writing: `481 passed` across the full suite,
`136` of them in the live-intelligence layer and `41` in the new Stage-13
layer. Live evidence: Arm A smoke (44 polls / 12 decisions) and Arm C
verification (34 polls / 10 decisions / 1 real provider call) against Binance
public data, both with a verified ledger and a clean shutdown.
