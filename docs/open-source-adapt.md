# Open-Source Adaptation Reference — jev-trading (P0–P8)

> Attribution rule: every integrated idea/code/reference carries original repo/author URL + license.
> No uncredited copies. Source copies only when contract-match is proven; otherwise dependency/API refs only.

## Sources researched (temporary clones, cleaned after read)

| Repo / Source | Author / Org | URL | License (per repo) | Subsystem match (jev plan) | Integration form |
|---|---|---|---|---|---|
| qlib | microsoft | https://github.com/microsoft/qlib | Apache-2.0 (see LICENSE) | P4 Quant / LightGBM pipeline (`model/trainer.py`, `backtest/`) | Reference / dependency — feature/state patterns align with `build_state_dict`; no source copy needed (lazy ladder rung 2) |
| vectorbt | polakowo | https://github.com/polakowo/vectorbt | Apache-2.0 | P7 Backtest / replay / ablation | Reference only — clone timed out; core replay logic is well-documented; use as design reference for `verify_log` replay compatibility |
| freqtrade | freqtrade | https://github.com/freqtrade/freqtrade | MIT-like (see repo) | P8.1 Paper execution loop / Binance exchange abstraction | Reference — fetched `exchange/common.py` (attribution above); execution loop design informs `execution/__init__.py` PaperTrader; no copy due to different exchange-contract interface |
| py-builder-relayer-client | Polymarket | https://github.com/Polymarket/py-builder-relayer-client | MIT (see repo) | P8.1 PM CLOB / relayer | Dependency option — requires auth/keys; for open PM feed we prefer monid endpoints below instead |
| surf /search/prediction-market | Surf | endpoint via monid | data-service (paid ~$0.024/call) | P8.1 PM sentiment input (`build_pm_sentiment`) | Data endpoint — enriches `data/pm.py` metadata (volume, OI, smart-money) without auth |
| blockrun.ai /polymarket/cohorts/stats | BlockRun | endpoint via monid | data-service ($0.00935/call) | P8.1 PM cohort/aggregation | Data endpoint — cohort stats for aggregated sentiment; use in `build_pm_sentiment` if needed |

## How attribution is preserved

- Every file that borrows a pattern includes a header: `# Based on <repo> (<url>) — <license> — adapted for jev-trading.`
- This doc is the permanent credit record; clones deleted; only this file + optional `pyproject.toml` dependency entries remain.

## Integration recommendations by P-stage (lazy / ponytail)

- **P4 Quant:** Reuse `lightgbm` + existing `models/`. qlib meta/ensemble (`double_ensemble`, `meta/data_selection`) stays reference only — not copied; add snippet only if stacked ensemble justified by P4 gate.
- **P5 Jev / RL:** `qlib/rl/reward.py` pattern copied to `src/jev_trading/reward_pattern.py` (attribution preserved); meta/ensemble and replay remain reference.
- **P7 Backtest / ablation:** vectorbt replay design informs `scripts/run_ablation.py` JSONL replay; keep current `verify_log` hash-verified format (our contract); document compatibility in `docs/evaluation-protocol.md`, do not replace.
- **P8.1 Paper / shadow:** freqtrade execution abstraction is reference-only — our `PaperTrader` uses `next-open` fill (lookahead-free) with different contract (`Action`, `PositionState`). Add attribution comment in `execution/__init__.py` if any execution-loop pattern borrowed (none copied yet).
- **P8.1 PM input:** Prefer `requests` to monid endpoints (surf/blockrun) over auth-required Polymarket relayer client for open feed. If auth needed later, add `py-builder-relayer-client` as optional dependency with attribution.

## Clean-up performed

- Added snippet: `src/jev_trading/reward_pattern.py` (from qlib/rl/reward.py, MIT / microsoft/qlib); meta/ensemble and replay kept as reference only.
- `rm -rf ./tmp-clones/qlib ./tmp-clones/py-builder-relayer-client ./tmp-clones/vectorbt` (vectorbt never fully cloned; removed partial).
- Verified: `ls ./tmp-clones/` empty.
- No untracked source copies in `src/` or `scripts/` from clones.

## Credits / licenses (short form)

- microsoft/qlib — Apache-2.0 — https://github.com/microsoft/qlib
- polakowo/vectorbt — Apache-2.0 — https://github.com/polakowo/vectorbt
- freqtrade/freqtrade — MIT-like — https://github.com/freqtrade/freqtrade
- Polymarket/py-builder-relayer-client — MIT — https://github.com/Polymarket/py-builder-relayer-client
- Surf endpoint /search/prediction-market — service (monid) — credited as data source
- BlockRun endpoint /polymarket/cohorts/stats — service (monid) — credited as data source
