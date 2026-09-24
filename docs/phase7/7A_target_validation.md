# Phase 7A historical target audit (superseded)

This was an inspection of the earlier Phase 6/7 code path, not a valid economic
validation. The following limitations are material:

- The historical economic layer double-counted slippage: its base round-trip
  cost already included fee plus slippage, then added slippage again.
- Funding is a per-eight-hour quote in the bar contract, but the historical
  approximation charged it as if it repeated every held minute and several
  callers passed no row funding.
- The historical minimum-edge multiplier was read from the wrong configuration
  location and fell back silently.
- The historical regression/excursion fields were synthetic approximations in
  the experiment runners, not trained predictions.
- The binary model has supported validation calibration evidence from the
  current pre-OOS freeze; the historical 2025 artifacts do not establish OOS
  calibration or OOS predictive ranking.

The current corrected cost logic is implemented in
`src/jev_trading/quant/economic.py` and checked by `tests/test_economic.py`.
The current Phase 0 baseline and Phase 1 label/head experiment are the admissible
evidence paths. No conclusion in this historical file should be used to select
features, thresholds, models, or Jev behavior.
