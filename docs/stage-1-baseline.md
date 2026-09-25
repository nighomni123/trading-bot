# Stage 1–3 baseline — Ling typed-contract investigation

Recorded before implementing the tool-call interface.

## Git

```text
SHA: 0da01beb91b37a6b0a97ee1a73f3a4d9f8b30da2
Branch: main
Remote main: 060e12bbcc5bbdee030738bba8e18a035f78348
Real orders sent: NO
```

## Tests

```text
321 passed
0 failed
0 skipped
```

## Provider smoke

Command:

```bash
.venv/bin/python -m jev_trading.live_intelligence provider-smoke \
  --component both --config configs/live.json
```

Both components reached OpenRouter with HTTP 200 and produced no execution. The default smoke output was:

```text
Frontier: connectivity PASS, parsing PASS, schema validation FAIL, trading NOT_INVOKED
Jev:     connectivity PASS, parsing PASS, schema validation FAIL, trading NOT_INVOKED
```

A separate diagnostic showed the response is nondeterministic:

- At the configured output budget, both responses had `content=None` with substantial `reasoning` content. Classification: `EMPTY_OUTPUT`.
- At a larger diagnostic output budget, both returned JSON that parsed but failed the current domain schema.

## Exact Frontier schema failures

The model returned a partial object with no `hypothesis_id`, `timestamp`, `regime`, `regime_confidence`, `thesis`, `reason`, `model_version`, or `prompt_version`; it also supplied free-text `jev_questions` instead of `JevQuestion` objects and an unexpected `reasoning` field.

```text
MISSING_REQUIRED_FIELD: hypothesis_id
MISSING_REQUIRED_FIELD: timestamp
MISSING_REQUIRED_FIELD: regime
MISSING_REQUIRED_FIELD: regime_confidence
MISSING_REQUIRED_FIELD: thesis
MISSING_REQUIRED_FIELD: reason
MISSING_REQUIRED_FIELD: model_version
MISSING_REQUIRED_FIELD: prompt_version
INVALID_NESTED_OBJECT: jev_questions[0..3] were strings, not JevQuestion objects
UNEXPECTED_FIELD: reasoning
```

## Exact Jev schema failures

The model returned a partial object with no `decision_id`, `request_id`, `timestamp`, `valid_until`, `confidence`, `recommended_state`, `model_version`, or `prompt_version`.

```text
MISSING_REQUIRED_FIELD: decision_id
MISSING_REQUIRED_FIELD: request_id
MISSING_REQUIRED_FIELD: timestamp
MISSING_REQUIRED_FIELD: valid_until
MISSING_REQUIRED_FIELD: confidence
MISSING_REQUIRED_FIELD: recommended_state
MISSING_REQUIRED_FIELD: model_version
MISSING_REQUIRED_FIELD: prompt_version
UNEXPECTED_FIELD: verdict
UNEXPECTED_FIELD: metrics
UNEXPECTED_FIELD: concerns
UNEXPECTED_FIELD: positives
UNEXPECTED_FIELD: recommendation
```

The current strict domain schemas are therefore not reliably produced by the model. No domain validation was weakened and no fields were fabricated.

## Baseline conclusion

The blocker is an interface/structured-output mismatch, not evidence of trading value. The next stage must compare a smaller model-facing contract through explicit tool calling while preserving the existing domain models and deterministic policy/risk authority.
