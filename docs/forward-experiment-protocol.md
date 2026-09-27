# Pre-registered protocol: forward A/B/C experiment

**Status: PRE-REGISTERED. Nothing in this file may be changed after the first
forward decision is committed to a ledger.** If a threshold turns out to be
wrong, that is a finding about the protocol, and the fix is a second
pre-registration, not an edit to this one.

Date registered: see the commit that adds this file.
Baseline commit: `3c2cc04` (branch `experiment/venue-clock-and-bounded-proposal`).

## The question

> Does adding Frontier (arm B) or Frontier + Jev (arm C) to the deterministic
> quant baseline (arm A) improve **forward, after-cost** outcomes enough to
> justify its complexity and expense?

Not: "can the model produce plausible commentary", and not: "is the architecture
interesting". Only the forward after-cost result counts.

## Why forward and not historical

A frontier model may already have seen part of any historical window, so a
backtest measures memorisation at least as much as judgement. Every number that
matters here therefore comes from data that arrived *after* the model and prompt
were frozen. The repo's own prior work reached the same conclusion for its own
models: out-of-sample economics were negative.

## Frozen configuration

Frozen by `configs/forward-hourly.json`, and stamped into every
`DecisionRecord.versions` so the freeze is verifiable after the fact:
`git_commit`, `config_hash`, `frontier_prompt_hash`, `jev_prompt_hash`,
`experiment_arm`, policy and risk hashes, strategy-registry hash.

| Setting | Value | Why |
|---|---|---|
| Instrument | `BTCUSDT_PERP` | one instrument; adds no execution surface |
| Positions | 1, concurrent | matches the single-position ledger |
| Model cadence | 1 call / 3600 s | slow cadence: less latency pressure, measurable inference cost |
| Decision loop | every completed 1m bar | deterministic protection runs continuously between model calls |
| Intended holding | up to 14400 s (4 h) | hours, not seconds |
| Prompt / model | frozen; no revision, no switching | switching mid-window destroys comparability |
| Providers | configured at deployment; recorded in the manifest | model identity is part of the result |
| Execution | PAPER only | no live order path exists or is authorised |

Abstention is a valid, unpenalised choice at every point. An arm that trades
less and does better has found something; an arm that trades more has not.

## Arms

Each arm runs as a **separate process against its own ledger and its own
paper account**, under a distinct `experiment_id` so the ledgers cannot mix.

- **A — Quant only.** Deterministic 1h-trend baseline. No model call at all.
- **B — Quant + Frontier.** A proposes; deterministic policy, risk and paper
  execution apply.
- **C — Quant + Frontier + Jev.** B plus the Jev evaluator and its gates.

## Benchmarks (not optional)

Stating an arm's P&L without these makes a long-biased run during a rally
indistinguishable from skill.

1. **Cash** — 0% return, no cost, no edge.
2. **Buy-and-hold BTC** over the identical window, from the identical bars,
   charged the identical fee and slippage.

## Pass / fail

An arm is **supported** only if all of the following hold. Any one failing is a
FAIL, and `jev report` states which.

1. At least **2 completed round trips**. Fewer trades cannot distinguish skill
   from luck, and a run that produces almost none is itself the finding.
2. **Net after-cost P&L > 0**, where costs are trading fees, modelled
   slippage, visible funding **and inference spend**.
3. **Beats passive buy-and-hold** on net P&L over the same window.
4. **Incremental value over arm A** is positive after model cost. A model arm
   that merely matches the deterministic baseline has added cost, not value.
5. **Drawdown** within the configured `maximum_drawdown_pct`.
6. Model cost is **measured, not assumed** — token counts are recorded. If the
   provider does not report usage, the run is reported as unpriced and cannot
   be declared a pass.

The decisive comparison is `C − A` and `B − A` after model cost.

## What a pass does and does not mean

A pass means the model's decisions earned capital after costs over this window.
It does **not** establish a durable edge, and it does not authorise real money.

A few profitable trades validate plumbing, not an edge. Only a result that
survives this protocol *and* replicates on a second, untouched window is
evidence worth funding further.

## Explicitly out of scope

- **No live execution.** The paper-only guards are unchanged: `ExecutionMode`
  has only `PAPER`, and `ExecutionState`, `ExecutionIntent` and
  `LiveSettings.validate_paper_only` each reject any other mode. A live adapter
  is a separate build behind its own gate.
- **No options.** Requires executable chain data, multi-leg portfolio
  accounting and portfolio risk. The single-position ledger must not carry
  spreads.
- **No new instruments.** ETH stays on its development track.
- **No prompt or model revision mid-window.**
