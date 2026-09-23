#!/usr/bin/env python3
"""EXP-004 real Jev attribution runner (Pass 1 / validation & OOS).

Controlled arms per docs/exp-004-real-jev.md:
  A — Quant baseline (candidate gate, direct entry; no policy, no Jev)
  B — Quant + deterministic policy (candidate gate + policy with neutral Jev)
  C — Quant + Jev (candidate gate + MockJev + policy with answers)
  D — Quant + Policy + Jev (same as C; full intended stack — distinction documented)

Preserves invariants:
  * Risk authority: RiskKernel evaluates every proposal; Jev never orders/risk/size.
  * Point-in-time: state built from row i only; no future keys permitted (adapter guard).
  * Execution: decision at bar t fills at open[t+1] (simulator next-bar rule).
  * Reproducibility: seed + config hash + model version + data hash recorded.
  * Candidate gate: p_up >= threshold chosen on validation, frozen for OOS.

Not a replacement for simulator; reuses prepare() / build_state_dict / RiskKernel.
MockJev is the test-double backend; result labeled as adapter+mock evidence only.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, math, os, sys, time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import polars as pl
import numpy as np
from jev_trading.backtest.simulator import prepare, build_state_dict, SimConfig, _fill_price, _fee, verify_log, Action, PositionState
from jev_trading.policy.engine import decide, load_config
from jev_trading.risk.kernel import RiskKernel
from jev_trading.jev.adapter import AdapterJev
from jev_trading.jev.mock import MockJev
from jev_trading.jev.questions import MVP_QUESTIONS
from jev_trading.state.features import FEATURE_COLUMNS
from jev_trading.quant.model import load_models

EXP_ID = "EXP-004"
VERSION = "1.0"


def _hash_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_provenance(args, git_sha: str, model_dir: str) -> dict:
    cfg_path = Path("experiments/EXP-004/config.yaml")
    cfg_text = cfg_path.read_text() if cfg_path.exists() else ""
    # data hash from raw parquet (fast partial)
    data_path = Path("data/btcusdt_1m.parquet")
    data_hash = "missing"
    if data_path.exists():
        # sample-based hash for speed; full hash too slow; document limitation
        import hashlib
        h = hashlib.sha256()
        # read first 5000 + last 5000 rows to detect major changes
        df = pl.read_parquet(str(data_path))
        h.update(str(len(df)).encode())
        h.update(df.head(5000).to_numpy().tobytes()[:10000])
        h.update(df.tail(5000).to_numpy().tobytes()[:10000])
        data_hash = h.hexdigest()[:16]
    return {
        "experiment_id": EXP_ID,
        "version": VERSION,
        "git_sha": git_sha,
        "config_hash": _hash_text(cfg_text),
        "data_hash": data_hash,
        "feature_version": "FEATURE_COLUMNS frozen",
        "label_version": "H=15m threshold=0.0014",
        "model_version": str(model_dir),
        "jev_version": "MockJev (test double)",
        "jev_question_set_version": "MVP_5",
        "candidate_threshold": args.candidate_threshold,
        "cost_model": "configs/costs.json",
        "execution_model": "next-open + slippage + funding",
        "seed": args.seed,
        "train_window": ["2021-01-01", "2024-01-01"],
        "validation_window": ["2024-01-01", "2025-01-01"],
        "test_window": ["2025-01-01", "2026-01-01"],
        "run_timestamp": _now_iso(),
        "locked": args.final_oos,
        "arms": args.arms.split(","),
    }


def run_arm(feats: pl.DataFrame, p_up: np.ndarray, arm: str, cfg: SimConfig,
            jev_client, threshold: float, policy_cfg: dict, log_path: Path | None = None,
            versions: dict | None = None) -> dict:
    """Run one EXP-004 arm over precomputed features + probs.

    Candidate gate: only bars where p_up >= threshold proceed to decision.
    Non-candidates always produce NO_ACTION.
    """
    assert arm in ("A", "B", "C", "D"), f"unknown arm {arm}"
    risk = RiskKernel(cfg.risk_config_path) if cfg.risk_config_path else RiskKernel()
    pos = PositionState(0, 0.0, 0.0, 0.0, 0.0, 0)
    capital = cfg.capital_usd
    cum_fee = cum_fund = peak = max_dd = 0.0
    wins = entries = exits = 0
    prev_fund = float(feats["funding_rate"].to_numpy()[0])
    records: list[dict] = []
    open_log = open(str(log_path), "w") if log_path else None
    if open_log:
        open_log.write(json.dumps({
            "type": "header", "schema_version": 2, "arm": arm,
            "cfg": {k: v for k, v in cfg.__dict__.items() if not k.startswith("_")},
            "versions": versions or {}, "experiment": EXP_ID
        }) + "\n")

    ts = feats["timestamp"].to_list()
    opens = feats["open"].to_list()
    closes = feats["close"].to_list()
    funds = feats["funding_rate"].to_list()
    n = len(feats)
    # Precompute candidates
    candidates = np.array([float(p_up[i]) >= threshold for i in range(n)], dtype=bool)

    for i in range(n - 1):  # need i+1 for next-bar fill; last bar cannot execute
        # Funding change on hold
        if funds[i] != prev_fund and pos.side:
            cum_fund += -pos.side * pos.quantity * closes[i] * funds[i]
        prev_fund = funds[i]
        # Daily reset (approx by calendar day from timestamp ms)
        if i > 0 and ts[i] // 86_400_000 != ts[i - 1] // 86_400_000:
            risk.reset_daily()

        # Candidate gate: if not candidate, skip all decision logic (NO_ACTION)
        is_cand = candidates[i]
        action = Action.NO_ACTION
        fill_px = 0.0
        fill_qty = 0.0
        fee = 0.0
        inputs: dict = {"p_up_15": round(float(p_up[i]), 6), "candidate": bool(is_cand)}
        state = {}
        jev = {}

        if is_cand:
            # Build minimal point-in-time state only for candidate bars (economically realistic)
            # Use build_state_dict on current row
            row_dict = feats.row(i, named=True)
            state = build_state_dict(row_dict) if arm in ("C", "D") else {}
            # Jev call only on candidates (selectivity measurement)
            if arm == "C" or arm == "D":
                # Minimal justified context (§9)
                state_for_jev = {
                    "market_state": {
                        "trend_score": float(row_dict.get("trend_score") or 0.0),
                        "volume_z": float(row_dict.get("volume_z") or 0.0),
                        "funding_z": float(row_dict.get("funding_z") or 0.0),
                        "vol_regime": float(row_dict.get("vol_regime") or 0.0),
                    },
                    "quant_predictions": {
                        "p_up_15": float(p_up[i]),
                        "p_dn_15": round(1.0 - float(p_up[i]), 6),
                        "expected_return_15": float(row_dict.get("ret_15m") or 0.0),
                    },
                    "candidate_side": "long" if float(p_up[i]) >= 0.5 else "short",
                    "candidate_policy": f"p_up_15 >= {threshold}",
                    "recent_context": f"trend={row_dict.get('trend_score')}, vol_regime={row_dict.get('vol_regime')}",
                }
                # Adapter enforces point-in-time guard (forbidden future keys rejected)
                answers = jev_client.ask(state_for_jev, MVP_QUESTIONS)
                jev = {a.name: round(a.value, 6) for a in answers}
                inputs["jev_answers"] = jev
                inputs["state_hash"] = hashlib.sha1(json.dumps(state_for_jev, sort_keys=True).encode()).hexdigest()[:16]
            elif arm == "B":
                # Deterministic policy without Jev-dependent info: neutral answers disable Jev conditions
                jev = {"trade_ok": 0.5, "failure_regime": 0.5, "breakout": 0.5, "liquidity_ok": 0.5, "vol_risk": 0.5}
                inputs["jev_answers"] = jev

            # Decision per arm
            if arm == "A":
                # Quant baseline: no policy intelligence; enter if candidate
                action = Action.ENTER_LONG
                inputs["decision_rule"] = "quant_baseline"
            else:
                # B/C/D use policy engine
                quant_input = {"p_up_15": float(p_up[i]), "expected_return_15": float(row_dict.get("ret_15m") or 0.0)}
                # For B use a cfg that neutralizes Jev conditions except via p_up (same as simulator policy arm)
                if arm == "B":
                    cfg_local = copy.deepcopy(policy_cfg)
                    cfg_local["enter_long"]["p_up_15"] = threshold
                    cfg_local["enter_long"]["jev_trade_ok"] = 0.0  # always passes
                    cfg_local["enter_long"]["jev_failure_max"] = 1.0  # always passes
                    d = decide(quant_input, {"trade_ok": 0.5, "failure_regime": 0.5}, cfg_local)
                else:
                    # C/D use answers; default cfg uses jev thresholds from policy.json
                    d = decide(quant_input, jev, policy_cfg)
                if d.action == Action.ENTER_LONG:
                    action = Action.ENTER_LONG
                    inputs["policy_proposal"] = d.action.value
                    inputs["policy_reasons"] = d.reasons
                else:
                    action = Action.NO_ACTION
                    inputs["policy_proposal"] = d.action.value
                    inputs["policy_reasons"] = d.reasons

            # Risk authority: every proposed entry/exits evaluated; never bypassed
            if action != Action.NO_ACTION and pos.side == 0 and action == Action.ENTER_LONG:
                prop = {
                    "action": action.value,
                    "confidence": 0.5,
                    "suggested_size_btc": cfg.size_btc,
                }
                port = {
                    "position_btc": 0.0,
                    "capital_usd": capital,
                    "daily_pnl_usd": 0.0,
                    "open_orders": 0,
                }
                mkt = {"spread_bps": cfg.spread_bps, "last_update_ms": ts[i], "now_ms": ts[i]}
                rd = risk.evaluate(prop, port, mkt, ts[i], closes[i])
                if rd.allowed:
                    fill_px, slip = _fill_price(opens[i + 1], 1, cfg)
                    fill_qty = rd.approved_size_btc
                    fee = _fee(fill_qty * fill_px, cfg)
                    pos = PositionState(1, fill_qty, fill_px, 0.0, pos.realized_pnl, 0)
                    entries += 1
                    action = Action.ENTER_LONG  # confirmed after risk
                else:
                    action = Action.NO_ACTION
            elif action == Action.NO_ACTION and pos.side != 0:
                # Exit if holding (desired flat) — same as simulator desired logic simplified
                prop = {"action": Action.EXIT.value, "confidence": 0.5, "suggested_size_btc": 0.0}
                port = {
                    "position_btc": pos.side * pos.quantity,
                    "capital_usd": capital,
                    "daily_pnl_usd": 0.0,
                    "open_orders": 0,
                }
                mkt = {"spread_bps": cfg.spread_bps, "last_update_ms": ts[i], "now_ms": ts[i]}
                rd = risk.evaluate(prop, port, mkt, ts[i], closes[i])
                if rd.allowed:
                    fill_px, slip = _fill_price(opens[i + 1], -1, cfg)
                    gross = pos.side * pos.quantity * (fill_px - pos.entry_price)
                    fee = _fee(pos.quantity * fill_px, cfg)
                    wins += gross > 0
                    pos = PositionState(0, 0.0, 0.0, 0.0, pos.realized_pnl + gross, 0)
                    exits += 1
                    action = Action.EXIT
                else:
                    action = Action.NO_ACTION

        # Record (always, not only on action — temporal audit)
        if pos.side:
            pos.time_in_position += 1
            pos.unrealized_pnl = pos.side * pos.quantity * (closes[i] - pos.entry_price)
        equity = capital + pos.realized_pnl + (pos.side * pos.quantity * (closes[i] - pos.entry_price) if pos.side else 0.0) - cum_fee - cum_fund
        peak = max(peak, equity) if (records or equity > 0) else equity
        max_dd = max(max_dd, (peak - equity) / peak if peak else 0.0)
        rec = {
            "ts": ts[i],
            "exec_ts": ts[i + 1] if (action != Action.NO_ACTION and i + 1 < n) else None,
            "arm": arm,
            "candidate": bool(is_cand),
            "p_up": round(float(p_up[i]), 6),
            **inputs,
            "decision": action.value if action else Action.NO_ACTION.value,
            "fill_px": round(fill_px, 4) if fill_px else 0.0,
            "fill_qty": fill_qty,
            "fee": round(fee, 6),
            "fund_rate": float(funds[i]),
            "pos": {"side": pos.side, "qty": pos.quantity, "entry": pos.entry_price, "unrealized": round(pos.unrealized_pnl, 4)},
            "equity": round(equity, 4),
            "cum_fee": round(cum_fee, 4),
            "cum_fund": round(cum_fund, 4),
            "seq": len(records) + 1,
        }
        rec["entry_hash"] = hashlib.sha256(json.dumps({k: v for k, v in rec.items() if k != "entry_hash"}, sort_keys=True, default=str).encode()).hexdigest()[:16]
        records.append(rec)
        if open_log:
            open_log.write(json.dumps(rec) + "\n")
        if fill_px:
            cum_fee += fee

    if open_log:
        open_log.close()
    net = records[-1]["equity"] if records else capital
    metrics = {
        "arm": arm,
        "n_bars": len(records),
        "n_candidates": int(sum(candidates[:len(records)])),
        "n_entries": entries,
        "n_exits": exits,
        "win_rate": wins / exits if exits else None,
        "fees": round(cum_fee, 4),
        "funding": round(cum_fund, 4),
        "net_pnl": round(net - capital, 4),
        "return_pct": round(100 * (net - capital) / capital, 4),
        "max_dd_pct": round(100 * max_dd, 4),
        "avg_per_exit": round((net - capital) / exits, 4) if exits else None,
        "exposure": round(sum(1 for r in records if r["pos"]["side"]) / len(records), 4) if records else 0.0,
        "threshold": threshold,
        "candidate_gate_active": True,
        "risk_authority": True,
    }
    return {"metrics": metrics, "records": records}


def compute_predictive_metrics(p_up: np.ndarray, y: np.ndarray | None = None) -> dict:
    """Calculate AUC / Brier / ECE if labels available (only when passed explicitly)."""
    if y is None:
        return {"auc": None, "brier": None, "log_loss": None, "note": "labels not provided to predictive metric (point-in-time safe)"}
    from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss
    try:
        auc = float(roc_auc_score(y, p_up))
    except Exception:
        auc = None
    probs = np.clip(p_up, 1e-6, 1 - 1e-6)
    try:
        bl = float(brier_score_loss(y, probs))
        ll = float(log_loss(y, probs))
    except Exception:
        bl = ll = None
    return {"auc": auc, "brier": bl, "log_loss": ll}


def main():
    parser = argparse.ArgumentParser(description="EXP-004 real Jev attribution")
    parser.add_argument("--start", default="2024-01-01", help="start YYYY-MM-DD")
    parser.add_argument("--end", default="2025-01-01", help="end YYYY-MM-DD (exclusive)")
    parser.add_argument("--candidate-threshold", type=float, default=0.40)
    parser.add_argument("--arms", default="A,B,C,D", help="comma-separated arms")
    parser.add_argument("--output-dir", default="experiments/EXP-004")
    parser.add_argument("--fee-mult", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--final-oos", action="store_true", help="OOS lock; no tuning allowed")
    parser.add_argument("--model-dir", default="models/")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    git_sha = os.popen("git rev-parse HEAD 2>/dev/null || echo unknown").read().strip()
    provenance = build_provenance(args, git_sha, args.model_dir)

    # Load data
    bars = pl.read_parquet("data/btcusdt_1m.parquet")
    feats, p_up_np = prepare(bars, load_models(args.model_dir))
    # Convert ts to ms
    feats = feats.with_columns((pl.col("timestamp") * 1000).alias("timestamp_ms"))
    # Slice by window
    import datetime
    start_ms = int(datetime.datetime.strptime(args.start, "%Y-%m-%d").timestamp() * 1000)
    end_ms = int(datetime.datetime.strptime(args.end, "%Y-%m-%d").timestamp() * 1000)
    keep = feats.filter((pl.col("timestamp") >= start_ms) & (pl.col("timestamp") < end_ms))
    # Need to align p_up with keep; prepare returns array aligned with feats after drop_nulls.
    # Since prepare drops null feature rows, direct index alignment is tricky; use timestamp-based merge.
    # For simplicity: rebuild features only for window (slightly slower but accurate)
    window_bars = bars.filter((pl.col("timestamp") >= start_ms) & (pl.col("timestamp") < end_ms))
    window_feats, window_p_up = prepare(window_bars, load_models(args.model_dir))
    # Note: prepare drops null features; window_p_up aligns with window_feats rows.
    feats = window_feats
    p_up = window_p_up
    n = len(feats)

    # Policy config
    policy_cfg = load_config()
    cfg = SimConfig(p_thr=args.candidate_threshold, fee_mult=args.fee_mult, seed=args.seed)

    # Jev adapter (test double clearly documented)
    adapter = AdapterJev(backend=MockJev())

    arm_results = {}
    for arm in args.arms.split(","):
        arm = arm.strip()
        log_path = out_dir / f"arm_{arm}.jsonl"
        res = run_arm(feats, p_up, arm, cfg, adapter, args.candidate_threshold, policy_cfg, log_path=log_path, versions=provenance)
        arm_results[arm] = res
        # Write metrics
        (out_dir / f"arm_{arm}_metrics.json").write_text(json.dumps(res["metrics"], indent=2))
        # Write candidate records (structured evidence log)
        cand_recs = [r for r in res["records"] if r.get("candidate")]
        (out_dir / f"arm_{arm}_candidates.jsonl").write_text("\n".join(json.dumps(r) for r in cand_recs))

    # Aggregate comparative metrics (primary attribution)
    comparison = {}
    for arm in args.arms.split(","):
        arm = arm.strip()
        m = arm_results[arm]["metrics"]
        comparison[arm] = {
            "net_pnl": m["net_pnl"],
            "return_pct": m["return_pct"],
            "max_dd_pct": m["max_dd_pct"],
            "trade_count": m["n_entries"],
            "win_rate": m["win_rate"],
            "fees": m["fees"],
            "n_candidates": m["n_candidates"],
        }
    # Incremental deltas
    deltas = {}
    for label, ctrl, treat in [("C - A", "A", "C"), ("D - B", "B", "D"), ("D - C", "C", "D")]:
        if ctrl in comparison and treat in comparison:
            deltas[label] = {
                "delta_net_pnl": round(comparison[treat]["net_pnl"] - comparison[ctrl]["net_pnl"], 4),
                "delta_return_pct": round(comparison[treat]["return_pct"] - comparison[ctrl]["return_pct"], 4),
                "delta_max_dd_pct": round(comparison[treat]["max_dd_pct"] - comparison[ctrl]["max_dd_pct"], 4),
                "delta_trades": comparison[treat]["trade_count"] - comparison[ctrl]["trade_count"],
                "delta_candidates": comparison[treat]["n_candidates"] - comparison[ctrl]["n_candidates"],
            }
    (out_dir / "incremental.json").write_text(json.dumps({"comparisons": comparison, "deltas": deltas}, indent=2))

    # Provenance
    (out_dir / "provenance.json").write_text(json.dumps(provenance, indent=2))
    # Config lock (if final-oos, write locked config; else just note)
    (out_dir / "locked_config.json").write_text(json.dumps({
        "locked": args.final_oos,
        "threshold": args.candidate_threshold,
        "arms": args.arms.split(","),
        "seed": args.seed,
        "fee_mult": args.fee_mult,
        "start": args.start,
        "end": args.end,
        "hash": _hash_text(json.dumps({"threshold": args.candidate_threshold, "arms": args.arms, "seed": args.seed, "fee_mult": args.fee_mult, "start": args.start, "end": args.end}, sort_keys=True)),
    }, indent=2))

    # Summary to stdout
    print(json.dumps({
        "experiment_id": EXP_ID,
        "window": [args.start, args.end],
        "final_oos": args.final_oos,
        "arms": args.arms.split(","),
        "results": comparison,
        "incremental": deltas,
        "note": "MockJev backend used; evidence about adapter+mock, not real evaluator quality.",
    }))


if __name__ == "__main__":
    main()
