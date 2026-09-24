# Phase 7 historical conclusion (not current OOS evidence)

## Scope and validity

These files document what the earlier Phase 7 scripts attempted. They are retained
for audit history, not treated as valid frozen-OOS experiments. The 2025 window
had already been consumed by EXP-004 and was reused here; it cannot become fresh
OOS again. The tracked runners for EXP-004, EXP-006, EXP-007B, and EXP-007C/7E
are retired so their invalid paths cannot be rerun accidentally.

## 7A — Economic target audit

The target and execution definitions were inspected, but the audit did not cure
the later cost/funding defects. In particular, the historical economic layer
used a base cost containing slippage and then added slippage again, treated a
per-eight-hour funding quote as a per-minute charge, and omitted row funding in
several calls. The corrected current implementation is covered by Phase 0/1
artifacts, not by this historical report.

## 7B — Historical target comparison (invalid as OOS evidence)

The stored EXP-007B artifacts are not a valid controlled OOS comparison:

- raw bars were sampled before feature/label construction, so the retained time
  series was discontinuous and the sample order was not a valid chronological
  split;
- regression rows were not purged at the train/validation boundary;
- the Q1 slice did not share the same warmup feature treatment as the later
  comparison;
- `E_updown` was a threshold transform of the 15m regression, not a trained
  multiclass UP/FLAT/DOWN model;
- the script constructed synthetic expected-return/excursion fields for its
  economic decisions.

The large predicted values therefore diagnose that historical implementation,
not a valid economic model result. The files remain historical evidence only.

## 7C/7E — Feature-family and edge-localization attempt (not an ablation)

The four stored family objects have different `columns_reference` fields but
identical numeric outcomes because the script reused one full-feature prediction
vector and did not retrain a model for each family. They therefore do not show a
feature-family ablation. The stored OOS window was also already consumed.

## Correct current evidence

- The current binary baseline is frozen in `EXP-008-phase0-baseline` and is
  reproducible on validation-only data.
- The current Phase 1 label/head experiment is the only admissible next economic
  measurement. It uses pre-2025 data, explicit baselines, and no frozen-OOS
  selection.
- No Frontier, Jev, RL, or additional data source should be introduced if that
  economic gate fails.

## Decision

**Historical Phase 7: REJECT as OOS/economic evidence.** Any future economic
signal must be re-established from a new locked experiment and a fresh untouched
holdout.
