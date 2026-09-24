# Phase 1 economic target gate — STOP

## Question

Can a trained economic target provide cost-adjusted information beyond simple
baselines on the untouched pre-OOS validation period?

## Controls and data

- Train: 2021-01-01 through 2023-12-31, with a 60-minute purge/embargo for the
  longest target.
- Validation: 2024 calendar year.
- Frozen/consumed 2025+ data: not read by the experiment.
- Baselines: zero, historical training mean, and ordinary linear regression.
- Intervention: deterministic LightGBM regressors for 5m/15m/30m/60m returns,
  15m/60m MFE, and 15m/60m MAE.
- TP/SL path diagnostic: 20bp / 20bp over 60 bars.

## Result

The return heads did not produce a positive mean predicted net edge after the
0.0014 round-trip cost:

| Target | LGBM corr | Mean predicted net edge | Decision |
|---|---:|---:|---|
| 5m return | 0.0399 | -0.0014003 | no useful economic edge |
| 15m return | 0.0184 | -0.0014249 | no useful economic edge |
| 30m return | -0.0187 | -0.0014505 | no useful economic edge |
| 60m return | 0.0285 | -0.0015437 | no useful economic edge |

The excursion heads did show conditional predictability (for example 15m MFE
correlation about 0.45), but excursion magnitude without a positive directional
net edge is not a tradable opportunity. The path diagnostic had 525,395 valid
rows, a 9.67% timeout rate, and roughly balanced first-touch outcomes.

## Decision

**STOP.** No specialist, abstention model, Frontier allocator, or Jev layer is
justified by this evidence. The binary/current baseline remains the only frozen
research reference. Revisit features/targets or obtain a genuinely untouched
holdout before attempting another promotion experiment.

Artifacts: `experiments/EXP-009-economic-targets/` and
`artifacts/phase1-economic-run1/`.
