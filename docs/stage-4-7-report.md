# Stage 4–7 report — provider reliability, checkpoint integrity, decision benchmark, first controlled A/B

Repository state at start: `687cd52`, 327 tests passing, working tree clean, no real or paper orders.

## Stage 4 — Provider Reliability

### 4.1 Audit of the pre-existing retry layer

Before this stage the clients already had bounded retries (`max_retries`, default 2), classified errors, did not retry 4xx, and recorded provider/model/category/status/retry-count/request-id on failure. What was missing: jitter, `Retry-After` support, a delay cap, and configurable concurrency/minimum interval for bursty experiment workloads. This stage added those rather than a second retry layer.

### 4.2 Retry policy now in force

```text
Retryable: 429, 408, 5xx, transport failures, timeouts
Non-retryable: 401, 403, 400/404/422, schema/validation, configuration
```

- Bounded exponential backoff with jitter: `delay = min(cap, base * 2^attempt) * (1 + U(0, 0.25))`.
- `cap` is `max_retry_delay_seconds` (default 30s, config-bound, range 1–300s).
- A provider `Retry-After` header, when present and parseable, wins over the exponential schedule and is itself capped.
- Non-retryable errors fail on the first attempt with no sleep.
- Telemetry records `retry_after`, `chosen_delay`, `retry_count`, `http_status`, `category`, `request_id`.

Implemented in `live_intelligence/provider_errors.py` and used by both clients (fix once, both callers).

### 4.3 Duplicate intelligence decisions

Retries happen *inside* one client call, so a retried request yields exactly one returned decision. Beyond that, decision identity is now deterministic:

```text
request_id / decision_id = sha256(experiment_id | decision_timestamp)[:32]
```

- `PolicyFinalizer.finalize` receives this deterministic `decision_id`.
- `DecisionLedger.append_decision` already rejects a duplicate `decision_id`, so a replay cannot append a second record.
- `ShadowRunner.run_once` short-circuits and returns the already-committed decision when the same `(experiment, decision time)` is seen, so resume never re-decides or re-queries.

A regression test asserts a 3-attempt retry produces exactly one decision, and a recovery test asserts resume performs zero provider calls.

### 4.4 Concurrency and sustainable benchmark mode

```bash
python -m jev_trading.live_intelligence provider-benchmark \
  --cases N --workers W --delay SECONDS
```

`--delay` implements a global minimum request interval (process-wide lock + monotonic clock), not a per-worker sleep. The benchmark now records `rate_limited`, `retries`, `p50/p95/p99`, and `requests_per_minute`.

### 4.5 Sustainable Ling benchmark result

```text
mode: workers=1, delay=20s, 4 cases per component (8 requests), 60s cooldown
total: 8
valid: 0
rate_limited (HTTP 429): 8
retries: 24 (3 per request, bounded)
validation failures: 0
trading execution: NOT_INVOKED
requests/minute: 3.37
```

**Result: the free endpoint is in a sustained 429 window.** At the lowest practical concurrency every request still returned 429 after bounded retries. This is not a client burst artifact. The reliability layer behaved correctly — it classified, retried within bounds, and failed closed with zero execution — but the provider is currently unavailable for live decisions.

Raw result: `docs/provider-sustainable-benchmark-2026-09-25.json`.

### Stage 4 conclusion

The reliability mechanics are correct and covered by tests (`tests/test_live_intelligence_provider_reliability.py`). Zero 429s are not required. The requirement that a 429 be handled safely and predictably is met. The remaining limitation is external: the free model is currently rate-limited.

## Stage 5 — Checkpoint and crash recovery

### 5.1 Checkpoint contract (as implemented)

```text
Checkpoint (schema_version 1)
├── experiment_id            (new; hard-fails on explicit mismatch)
├── ledger_hash              (anchor; mismatch no longer fatal)
├── position                 (PositionState)
├── account                  (AccountState)
├── execution                (ExecutionState)
├── pending                  (PendingIntentState | null)
├── trading_day
└── paper                    (paper executor state)
```

Not persisted, and deliberately so: market cursor, candidate cursor, random seed, per-strategy state. The runner is a pull loop over market data with no internal market cursor; candidates are recomputed from data, and there is no randomness. Adding unused fields would have been speculative.

### 5.2 The real gap that was fixed

The original code failed closed whenever the checkpoint trailed the ledger — including the ordinary crash window between `append_decision` and `_persist_checkpoint`. That made crash recovery impossible in the exact case it existed for.

Fix: the committed runtime state is now also written into the ledger record itself (`DecisionRecord.runtime_state`), using a single `_runtime_state()` source shared by the ledger record and the checkpoint. Restore now:

1. uses the checkpoint if present, well-formed, and anchored to the current ledger hash;
2. otherwise rebuilds from the newest ledger record carrying `runtime_state` (the ledger suffix);
3. hard-fails only when there is genuinely no recoverable state, or when the checkpoint explicitly belongs to a different experiment.

A missing `experiment_id` means a legacy/stale checkpoint and falls back; an explicit mismatch is dangerous and hard-fails.

Atomic writes were already correct (`tmp` + `fsync` + `os.replace` + directory `fsync`) and are left unchanged.

### 5.3 Intelligence replay

The ledger *is* the intelligence cache. With deterministic `request_id`, a resumed run finds the committed decision and reuses the stored Frontier hypothesis and Jev evaluation instead of re-querying the provider. Proven by `test_resumed_run_replays_intelligence_without_calling_provider`, which asserts zero Frontier and zero Jev calls on the second run and identical semantic records.

### 5.4 Crash-injection test results

```text
test_continuous_run_is_deterministic                            PASS
test_crash_after_ledger_commit_but_before_checkpoint_recovers    PASS
test_crash_with_missing_checkpoint_rebuilds_from_ledger          PASS
test_checkpoint_is_atomically_valid_and_anchored                PASS
test_checkpoint_from_another_experiment_is_rejected             PASS
test_resumed_run_replays_intelligence_without_calling_provider   PASS
```

Comparison is over decision id, hypothesis id, Frontier output, Jev output, policy decision, and risk decision.

**DETERMINISTIC RECOVERY: PASS**

Two pre-existing tests asserted the old hard-fail behaviour. They were updated to assert the new recovery behaviour, and a replacement test keeps the genuine fail-closed case (foreign experiment).

## Stage 6 — Historical decision-quality benchmark

### 6.1 Data and method

```text
history: data/btcusdt_1m.parquet
span: 2021-01-01 .. 2025-12-31, 2,629,440 one-minute bars
```

`decision_quality.py` enforces causality structurally: the model input is built from `causal_window()` (bars at or before the candidate timestamp only). Realized outcomes, MFE, and MAE are computed strictly *after* the candidate timestamp and are never part of any model request. `test_causal_window_never_contains_future_bars` pins this.

The evaluation slice is the most recent 60 evenly spaced candidate timestamps (3-day spacing). No prompt, threshold, quant, or risk parameter was tuned; the split is recorded for future use and nothing was fitted to the evaluation window.

### 6.2 Baseline characterization — the decisive result

QUANT_ONLY arm (deterministic quant + policy + risk, no LLM), 60 candidates, 15-minute horizon:

```text
candidates built:            60
target probability:          min 0.0000  median 0.0210  max 0.0880  (50/60 nonzero)
net expected value:          min -0.002476  median -0.001492  max -0.000880
candidates with positive NEV: 0
policy_eligible:             0 / 60
```

**The deterministic baseline selected none of 60 candidates.** With a 2:1 risk/reward target and 5 bps fees per side, the 15-minute target was essentially never reached often enough to produce positive net expected value. This faithfully reproduces the preserved historical `NO EDGE` result; it is a property of the current quant/policy configuration, not a regression.

Raw result: `docs/decision-quality-scan-2026-09-25.json`.

### 6.3 Candidate-level counterfactual dataset

The harness emits, per candidate, exactly the row structure required for a future training set: `candidate_id`, `timestamp`, `side`, `strategy`, `frontier_decision`, `frontier_abstain`, `jev_decision`, `baseline_eligible`, `treatment_eligible`, `changed`, `realized_return`, `mfe`, `mae`, plus `frontier_error` / `jev_error`.

No live-model Stage 6 run was completed: the treatment arm requires provider calls, and the provider is currently returning sustained 429s. A 3-candidate wiring check confirmed the treatment path executes and fails closed correctly under rate limiting, with `frontier_error`/`jev_error` populated and `execution: NOT_INVOKED`.

### 6.4 Incremental information — not measurable yet

The Stage 6 question ("does Frontier/Jev distinguish better candidates?") is **not answerable with the current configuration**, because the baseline rejects 100% of candidates. With nothing selected, there is no selection population to compare against an LLM-selected population. Manufacturing one by loosening thresholds, widening the horizon, or shrinking costs would be exactly the quant/threshold tuning this stage forbids.

No profitability claim is made.

## Stage 7 — First controlled A/B

### 7.1 Status

**NOT EXECUTED — BLOCKED.**

```text
experiment id: not assigned (no experiment was run)
baseline:  QUANT_ONLY            (harness verified, 0/60 eligible)
treatment: QUANT_FRONTIER_JEV    (wiring verified; live run blocked)
```

### 7.2 Why it was not run

Two independent blockers, either of which alone is sufficient:

1. **No selection opportunity.** The baseline selected 0 of 60 evaluation candidates, so a paired A/B on selection quality has no baseline population to compare against. Running it would produce a vacuous, uninterpretable result at the cost of many provider calls.

2. **Provider unavailable.** The free Ling endpoint returned HTTP 429 for every request at workers=1 with a 20-second minimum interval. A treatment arm cannot be populated from a provider that is refusing requests.

The A/B harness itself is implemented and exercised: `decision-quality` runs the baseline and treatment arms over one shared candidate stream, with identical economic assumptions, identical candidate ids, and no execution. It is ready to run the moment both blockers clear.

### 7.3 Safety

```text
REAL ORDERS SENT: NO
PAPER ORDERS SENT: NO
```

The A/B harness scores policy/risk eligibility and observed outcomes only. It never constructs an `ExecutionIntent` and never calls the paper executor.

### 7.4 What would unblock Stage 7

- A quant/policy configuration change that produces a non-empty baseline candidate population. This is a separate, explicitly out-of-scope experiment and must be versioned as such; it must not be smuggled in here.
- A provider that can sustain the request rate the decision cadence actually requires, or an explicit decision to accept a reduced cadence.

## Tests

```text
Full suite: 343 passed, 0 failed, 0 skipped
New this stage:
  tests/test_live_intelligence_provider_reliability.py   5
  tests/test_live_intelligence_checkpoint_recovery.py     6
  tests/test_live_intelligence_decision_quality.py        4
Updated:
  tests/test_live_intelligence_ledger_integrity.py        (recovery replaces hard-fail)
```

## Git

```text
HEAD before this stage: 687cd52
branch: main
working tree at report time: see git log below
```

No push was performed.

## Summary

| Stage | Status | Basis |
|---|---|---|
| 4 — Provider reliability | Mechanics PASS, provider 429-limited | Bounded retries, Retry-After, jitter, cap, no duplicate decisions; 8/8 rate-limited at workers=1 |
| 5 — Checkpoint integrity | **PASS** | Continuous == crash/resume, zero provider calls on resume |
| 6 — Decision benchmark | Baseline characterized; A/B not measurable | 0/60 eligible, NEV never positive |
| 7 — Controlled A/B | **NOT EXECUTED** | No baseline population + provider unavailable |

Stopping here as instructed. No optimization, no quant changes, no Laya training, no further experiments.
