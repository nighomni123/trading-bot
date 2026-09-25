# Stage 1–3 final report — typed Frontier/Jev tool-call interface

## 1. Baseline

Recorded before source changes in `docs/stage-1-baseline.md`:

```text
Baseline Git SHA: 0da01beb91b37a6b0a97ee1a73f3a4d9f8b30da2
Baseline tests: 321 passed, 0 failed, 0 skipped
Baseline Ling Frontier: HTTP 200, parsed output, schema failure
Baseline Ling Jev: HTTP 200, parsed output, schema failure
```

The baseline failures were caused by the full domain schemas, not by transport:

- Frontier omitted required identity/timestamp/regime/thesis fields, returned free-text Jev questions, and added an unexpected `reasoning` field.
- Jev omitted required identifiers/timestamps/confidence/state fields and returned `verdict`, `metrics`, `concerns`, `positives`, and `recommendation`, which are not domain fields.
- With the configured output budget, the reasoning model sometimes returned `content=None` and only reasoning text.

No domain validation was weakened.

## 2. Schema audit

| Field | Component | Classification | Reason |
|---|---|---|---|
| `hypothesis_id` | Frontier | DETERMINISTIC | Supplied by the application request |
| `timestamp` | Frontier | DETERMINISTIC | Supplied by the environment decision time |
| `regime` | Frontier | MODEL_REQUIRED | Strategic interpretation |
| `regime_confidence` | Frontier | MODEL_REQUIRED | Model confidence in the interpretation |
| `primary_strategy` / `strategy_family` | Frontier | MODEL_REQUIRED | Strategy selection is intelligence work |
| `direction` | Frontier | MODEL_REQUIRED | Research direction only, never execution |
| `horizon_seconds` | Frontier | DETERMINISTIC | Policy owns the allowed horizon |
| `thesis` | Frontier | MODEL_REQUIRED | Reasoning explanation |
| `entry_conditions` | Frontier | MODEL_OPTIONAL | Useful interpretation, not execution authority |
| `invalidation_conditions` | Frontier | MODEL_OPTIONAL | Strategic context |
| `target_logic` / `stop_logic` | Frontier | DETERMINISTIC | Candidate builder and risk own these |
| `maximum_holding_seconds` | Frontier | DETERMINISTIC | Policy configuration |
| `abandon_conditions` | Frontier | DETERMINISTIC | System safety rules |
| `jev_questions` | Frontier | MODEL_OPTIONAL | Current pipeline does not require them to trade |
| `abstain` | Frontier | MODEL_REQUIRED | Explicit no-trade option |
| `reason` | Frontier | DETERMINISTIC | Derived from thesis |
| `conviction` | Frontier | MODEL_REQUIRED | Alias for confidence |
| provider/model/prompt/schema metadata | Frontier | DOWNSTREAM_ONLY | Recorded by the application |
| `decision_id` / `request_id` | Jev | DETERMINISTIC | Supplied by the application request |
| `timestamp` / `valid_until` | Jev | DETERMINISTIC | Application clock and configured validity |
| `recommended_state` | Jev | MODEL_REQUIRED | Candidate evaluation decision |
| `abstain` | Jev | MODEL_REQUIRED | Explicit no-trade option |
| `confidence` | Jev | MODEL_REQUIRED | Model confidence |
| `entry_quality` | Jev | MODEL_REQUIRED | Candidate quality judgment |
| `failure_risk` | Jev | MODEL_REQUIRED | Failure assessment |
| `liquidity_quality` | Jev | MODEL_OPTIONAL | Model may assess context |
| `reason` | Jev | MODEL_REQUIRED | Explanation of evaluation |
| `target/stop/timeout probabilities` | Jev | DETERMINISTIC | Derived from canonical `QuantEvidence` |
| `target_probability` | Jev | DETERMINISTIC | Derived from Quant evidence |
| ratings/answers | Jev | DOWNSTREAM_ONLY | Optional audit metadata |
| `CandidateTrade` | Policy | DETERMINISTIC | Constructed from hypothesis/environment |
| `QuantEvidence` | Frontier/Jev | DETERMINISTIC | Computed by the quant layer |
| `MarketEnvironment` | Frontier/Jev | DETERMINISTIC | Constructed from causal data |
| `PolicyDecision` | Policy | DETERMINISTIC | Never model-generated |
| `RiskDecision` | Risk | DETERMINISTIC | Never model-generated |
| `ExecutionIntent` / `PaperFill` | Execution | DETERMINISTIC | Never model-generated |

Authority fields such as `quantity`, `position_size`, `notional`, `leverage`, `order`, `execute`, `account_balance`, and `risk_limit` are absent from both model-facing contracts and rejected by `extra="forbid"` validation.

## 3. New contracts

### `FrontierModelDecision`

```text
regime
strategy_family
direction
confidence
abstain
thesis
entry_conditions
invalidation_conditions
```

The adapter deterministically adds the hypothesis ID, decision timestamp, prompt/model/provider identity, policy horizon, target/stop logic, abandonment rules, and domain metadata.

### `JevModelDecision`

```text
recommended_state
abstain
confidence
entry_quality
failure_risk
liquidity_quality (optional)
reason
```

The adapter deterministically adds request/decision IDs, timestamps, validity, and target/stop/timeout probabilities from `QuantEvidence`. The model never calculates costs or sizes.

## 4. Tool interface

```text
Tool: submit_strategy_hypothesis
Required tool choice: yes, when supports_tool_calling=true
Schema source: FrontierModelDecision.model_json_schema(), with local $defs inlined

Tool: submit_jev_evaluation
Required tool choice: yes, when supports_tool_calling=true
Schema source: JevModelDecision.model_json_schema(), with local $defs inlined
```

Tool-call processing is strict:

```text
response
→ exactly one tool call
→ expected tool name
→ JSON arguments
→ typed contract validation
→ domain adapter
```

Missing, multiple, malformed, prose-only, wrong-tool, and unauthorized-field responses fail closed. The existing JSON path remains available for models that do not support tool calling.

## 5. Provider behavior

### Ling

```text
response_format: false
tool_calling: true
structured JSON path: failed baseline
typed tool path: passed
```

The model page reports text-only I/O, 262,144 context tokens, up to 32,768 output tokens, free pricing, and no `response_format`/structured-output support. Tool calling is supported.

### Nemotron

The public OpenRouter metadata endpoint was queried before any request. It reports text-only I/O, 1,000,000 context tokens, tool calling and reasoning support, and no `response_format`/structured-output support.

### `openrouter/free`

The metadata endpoint reports text/image input, 200,000 context tokens, `response_format`, `structured_outputs`, tool calling, and reasoning support. It is a fallback/integration profile, not a controlled model-quality comparison.

No successful live request is claimed for Nemotron or `openrouter/free`.

## 6. Benchmark

Command:

```bash
.venv/bin/python -m jev_trading.live_intelligence provider-benchmark \
  --cases 12 --workers 4 --config configs/live.json
```

Fixture design: 12 deterministic Frontier cases and 12 deterministic Jev cases covering directional, flat, expansion, and compression variants. The benchmark stops at typed intelligence output and never starts paper execution.

```text
Total cases:                  24
Valid decisions:              24
Validation failures:          0
Tool-call successes:         24
Authority violations:         0
Malformed/no-tool responses:  0
Latency p50:                  10,345.30 ms
Latency p95:                  18,500.52 ms
Trading execution:            NOT_INVOKED
```

A subsequent immediate smoke request was rate-limited by the free provider with HTTP 429 after the benchmark. The structured failure telemetry recorded the category, status, and retry count; no order path was invoked.

## 7. Tests

```text
Full suite: 327 passed, 0 failed, 0 skipped
Provider capability tests: passed
Tool-schema/adapter tests: passed
Authority-boundary tests: passed
Mock benchmark test: passed
```

## 8. Execution safety

```text
REAL ORDERS SENT: NO
PAPER ORDERS SENT: NO
```

The benchmark and provider smoke stop at typed Frontier/Jev decisions. Existing mocked paper-executor unit tests remain deterministic.

## 9. Recommendation

Ling is **conditionally suitable** for the current typed Frontier/Jev interface:

- The minimal tool-call contract produced 24/24 valid decisions in the deterministic benchmark.
- No authority fields reached downstream logic.
- The tool path solved the full-schema mismatch.
- Free-model rate limiting and roughly 18.5-second p95 latency make it unsuitable for continuous trading without additional rate-limit/backoff controls and a longer stability run.

This stage does not authorize A/B profitability experiments, threshold changes, quant changes, Laya training, or any live execution.
