# Stage 12 — Promotion Criteria (defined BEFORE any result is interpreted)

These criteria are fixed in advance. No numeric threshold is chosen after seeing
a result. A candidate that fails any criterion is not promoted regardless of how
large its effect looks.

## Gate 0 — Integrity (all mandatory, no exceptions)

1. No target-derived column in the model feature set (verified by the centralized
   firewall and by tests that corrupt future data).
2. Train/test split is chronological with an embargo >= the maximum target horizon.
3. No hyperparameter search; frozen model configuration.
4. No random split.
5. Production files unchanged.

## Gate 1 — Economic magnitude

The observed out-of-sample conditional gross edge, computed on a
**non-overlapping decision grid**, must satisfy:

```text
edge > execution_cost + funding + safety_margin
```

where `execution_cost` is the Stage 10 measured profile (11.006 bps round trip).
Reported as a ratio `edge / cost`. A ratio > 1 is **necessary, not sufficient** —
it only means the mean conditional edge exceeds assumed cost.

## Gate 2 — Multi-period stability

The effect must be directionally consistent across at least **3 of 5** independent
calendar years (2021–2025), with a positive mean in each. A sign flip in any year
that is individually significant (|t| > 2.58) marks the candidate **UNSTABLE**
regardless of the pooled result.

## Gate 3 — Multiple-testing control

Discovery is over many state/horizon combinations. The number of hypotheses
tested is reported, and Benjamini-Hochberg FDR at `q = 0.10` is applied. A
candidate must survive FDR adjustment, OR be reported as exploratory-only and
explicitly not promoted.

## Gate 4 — Sample adequacy

Per-year effective sample must be large enough for the reported statistic. Because
horizons overlap, effective n is the count on the non-overlapping grid, not the
raw row count. Candidates with fewer than 30 non-overlapping observations in a
year are `UNTESTED` for that year.

## Gate 5 — Perturbation robustness

The effect must not disappear under modest perturbation:

- state threshold +/-10% and +/-20%
- horizon +/-1 bar and +/-5%

A candidate whose sign flips under these is classified **FRAGILE** and is not
promoted.

## Gate 6 — Execution plausibility

The assumed cost profile must be achievable. `maker/maker` is not admissible
because fill probability is not modelled. Candidates whose edge survives only
under the non-achievable profile are `UNTESTED` on execution, not promoted.

## Gate 7 — Not a single event

The effect must not be driven by one extraordinary observation. Reported
alongside a leave-one-year-out check.

## Status vocabulary (strict)

```text
UNTESTED          not evaluated, or data unavailable
FALSIFIED         evaluated and does not meet the gates
WEAK              positive but fails at least one economic/integrity gate
INTERESTING       passes economics on one period, needs independent validation
REQUIRES_VALIDATION  meets gates on >=1 period, not yet multi-period
MULTI_PERIOD_CANDIDATE  passes all gates on >=3 independent years
```

`VALIDATED`, `PROFITABLE`, `WINNER`, `BEST` are **not** Stage 12 outcomes.

## Note on thresholds

The only numeric threshold used is the measured execution cost (11.006 bps) and
standard statistical levels (95%/99% t, FDR q=0.10). No profit target, no minimum
Sharpe, no minimum return is specified, because Stage 12 is not asked to produce
a tradeable strategy.
