# Phase 2 Economic Quant Engine v2 — final research report

## A. Current architecture

```text
1m bars
  → causal 16-feature baseline (+ separately tested derived features)
  → explicit down/flat/up multiclass direction
  → raw 5m/15m/30m/60m return heads
  → execution-aligned 5m/15m/30m/60m return heads
  → execution-aligned 15m/60m MFE/MAE heads
  → 20bp/20bp first-barrier holding-time head
  → three-seed return ensemble mean + normalized dispersion uncertainty
  → deterministic cost/uncertainty gate
  → research-only fixed-horizon simulation
```

The economic-v2 bundle is loadable through `QuantPredictor`, but it is **not**
connected to live execution, policy promotion, Frontier, Laya, or Jev.

## B. Models

| Head | Target | Result |
|---|---|---|
| Direction | `execution_return_15m` class: down/flat/up | Accuracy 0.5561, macro OVR AUC 0.6593, log-loss 0.9502, Brier 0.5600 |
| Raw returns | close-anchored 5m/15m/30m/60m | Correlations 0.0291 / 0.0189 / -0.0199 / 0.0278; no stable MAE lift over the mean |
| Execution returns | open[t+1] → open[t+H+1] | Base mean adjusted predicted edge: **-0.001402** |
| Excursions | execution MFE/MAE 15m/60m | 15m MFE corr 0.4324; 15m MAE corr 0.3866; conditional magnitude only |
| Holding | first 20bp TP/SL touch, 60m timeout | Real regressor; incomplete tail windows excluded |
| Uncertainty | normalized ensemble dispersion | Error non-monotonic; rank correlation 0.1 |

## C. Features

- Promoted baseline: the existing 16 causal features.
- Tested intervention: EMA20/50 relationships, trend acceleration/duration,
  return/ATR, return/realized-volatility, and funding×OI interaction.
- Derived features were **not promoted**: eligible bars fell from 35 to 16 and
  mean adjusted edge stayed negative.
- No microstructure family was added because bid/ask, order-book, trade-flow,
  spread-history, depth, and liquidation data are absent. EXP-013 does not claim
  a microstructure result.
- Momentum, mean-reversion, breakout, and ensemble experiments are `DEFER`, not
  successful or failed specialist results: their base economic prerequisite failed.

## D. Economic methodology

For side-adjusted predicted return `g`:

```text
fixed cost = 2×0.05% fee×fee_multiplier
          + 2×(0.02% slippage + extra_slippage)
          + delay_penalty

funding = side × funding_rate × holding_minutes / 480

predicted net = g − fixed cost − funding
uncertainty penalty = normalized ensemble dispersion × |g|
adjusted edge = predicted net − uncertainty penalty

TRADE only when:
  explicit side is the most probable non-flat class
  and adjusted edge ≥ 2 × scenario cost
```

Realized fixed-horizon trades use execution-aligned open-to-open returns. Feature
nulls split simulation into contiguous segments; gaps are never compressed into
shorter holding periods.

## E. Experimental results

These are validation/research numbers, not fresh OOS evidence: historical 2025+
has already been consumed.

| Experiment | Model | Validation trades | Net return sum | Expectancy | Sharpe | Max DD | 2× cost | 3× cost | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| EXP-010 | Base economic v2 | 9 | 0.0814 | 0.00904 | 0.636 | 0.0205 | 0 trades | 0 trades | NO EDGE |
| EXP-011 | + derived features | 5 | 0.0623 | 0.01246 | 0.527 | 0.0204 | 2 trades | 1 trade | NO EDGE |
| EXP-018 fold 2022 | 2021→2022 | 12 | 0.1414 | 0.01178 | 0.630 | 0.0046 | — | — | NO EDGE |
| EXP-018 fold 2023 | 2021–22→2023 | 5 | 0.0701 | 0.01403 | 1.702 | 0.0000 | — | — | NO EDGE |
| EXP-018 fold 2024 | 2021–23→2024 | 9 | 0.0870 | 0.00967 | 0.688 | 0.0205 | — | — | NO EDGE |

The selected validation subsets are positive, but the mean predicted adjusted
edge is negative in every walk-forward fold and only 5–12 trades are selected.
That is selection scarcity, not a robust economic signal. Under base 2×/3× fees
and extra slippage, the base engine selects zero trades.

## F. Regime analysis

All 35 base eligible bars and all 9 selected trades fall in the high-volatility,
high-volume, trending bucket. Funding regimes contain only a handful of eligible
bars. This is far too little evidence to justify regime-specific strategies, and
no regime threshold was tuned.

## G. Leakage audit

- Raw labels use `t+1..t+H`; execution labels enter `open[t+1]` and exit
  `open[t+H+1]`.
- Timestamp gaps are rejected during label construction and split into separate
  simulation segments rather than compressed.
- Every 30m/60m experiment uses a 60-minute train/validation boundary buffer.
- Features are rolling, EWM, shifted, or current-bar only.
- Realized returns are attached only after predicted-side selection.
- No 2025+ values were read by Phase 2 training or validation scripts.
- The 2025+ set remains historically consumed and is not claimed as fresh OOS.

## H. Promotion decision

# NO EDGE

The explicit heads and economic engine are implemented and tested, but the current
BTC 1-minute feature set does not produce repeatable positive mean net edge or a
meaningful stable trade population. Frontier/Laya/Jev promotion is not justified.

## I. Next phase

Do not add Frontier or Laya. The next valid work is a new hypothesis for the
return target or a new already-available causal feature family, followed by the
same pre-OOS experiment gates. Any eventual promotion also requires a genuinely
untouched future holdout.
