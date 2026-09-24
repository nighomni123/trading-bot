"""P7 event simulator: BAR -> STATE -> QUANT -> JEV -> POLICY -> RISK -> ORDER -> FILL -> POSITION -> PNL.

MVP conventions (documented; revisited in later P7 iterations, not silently changed):
- Next-bar execution: a signal from row t fills at open[t+1] +/- slippage.
  Same-bar close[t] fills are forbidden (lookahead) — docs/execution-semantics.md.
- Desired-position trading: each bar maps to desired +1 (long) or 0 (flat);
  NO_ACTION while holding means exit. No p_dn producer exists yet, so no shorts.
- Fixed suggested size per entry; the risk kernel clamps it (room/risk-cap/leverage).
- Funding is charged when the funding_rate print changes while holding:
  -side * qty * mark * rate (longs pay when the rate is positive).
- One schema-v3 JSONL record per bar behind a header line carrying component
  versions; a complete run ends with a footer and is checked by ``verify_log``.
  Bars whose features are undefined (e.g. zero-OI gaps) are skipped: the next
  valid row's open is the next tradeable moment, still strictly in the future.
- State dicts (and their hashes) are built only for the full arm — the only
  consumer of market state. Other arms log their true inputs (p_up / coin flip).
- `prepare()` builds features + quant probs ONCE; all arms share them (the caller
  computes once, `run()` only slices the window). `stride` is DEV ONLY: iterate
  every Nth bar with fills at the next iterated open — never for gate runs.

Arms (identical data/costs/splits; only the decision stack varies):
- A random: seeded desired-position coin flips -> risk -> costs.
- B threshold: raw quant rule (p_up >= thr -> long else flat) -> risk -> costs.
- C policy: quant + policy with Jev gates neutralized via cfg override -> risk.
- D full: quant + MockJev + default policy (matched p threshold) -> risk.
- E nojev: D's cfg with Jev answers forced neutral (expected: flat — documents
  that the policy does nothing without a Jev signal).
Jev's incremental contribution = Perf(D) - Perf(C).

ponytail: single-file MVP — one position, full exits only, no intrabar modeling.
Upgrade path: PositionState-aware policy exits, partial fills, short arm.
"""
from __future__ import annotations

import copy
import random
from hashlib import sha1
from dataclasses import asdict, dataclass
from pathlib import Path

import polars as pl

from jev_trading.contracts import Action
from jev_trading.events import entry_hash, verify_log, write_footer, write_header, write_record
from jev_trading.jev.questions import MVP_QUESTIONS
from jev_trading.policy.engine import decide, load_config
from jev_trading.risk.kernel import RiskKernel
from jev_trading.state.features import FEATURE_COLUMNS, build_features, build_state_dict

ARMS = ("random", "threshold", "policy", "full", "nojev")


@dataclass
class PositionState:
    """Open-position snapshot (field names per the P7 spec)."""

    side: int  # +1 long, -1 short, 0 flat
    quantity: float
    entry_price: float
    unrealized_pnl: float
    realized_pnl: float
    time_in_position: int  # bars since entry


@dataclass
class SimConfig:
    capital_usd: float = 10_000.0
    size_btc: float = 0.01  # suggested per entry; risk clamps to room/risk-cap/leverage
    spread_bps: float = 1.0  # fixed venue spread for the risk MarketCheck
    taker_fee_pct: float = 0.05
    slippage_pct: float = 0.02
    fee_mult: float = 1.0  # hostile-cost gate: 2-3x before P8
    p_thr: float = 0.40  # quant-probability threshold for arms B/C/D (P7 starting grid)
    seed: int = 7
    stride: int = 1  # DEV ONLY: iterate every Nth bar. Never for gate runs.
    risk_config_path: str | None = None  # optional risk.json override for ablation arms


def _fill_price(open_next: float, side: int, cfg: SimConfig) -> tuple[float, float]:
    """(fill price, slippage cost per BTC). Buys fill higher, sells lower."""
    slip = open_next * cfg.slippage_pct / 100
    return (open_next + slip, slip) if side > 0 else (open_next - slip, slip)


def _fee(notional: float, cfg: SimConfig) -> float:
    return abs(notional) * cfg.taker_fee_pct / 100 * cfg.fee_mult


def _state_hash(state: dict) -> str:
    import json
    return sha1(json.dumps(state, sort_keys=True).encode()).hexdigest()[:16]


def _desired(arm: str, p_up: float, jev: dict, cfg: SimConfig, rng: random.Random,
             policy_cfg: dict) -> tuple[int, dict]:
    """Desired position (+1/0) and the quant/jev inputs the decision used."""
    if arm == "random":
        return (1 if rng.random() < 0.02 else 0), {}
    if arm == "threshold":
        return (1 if p_up >= cfg.p_thr else 0), {"p_up_15": p_up}
    if arm == "policy":  # quant -> policy, Jev gates neutralized so only p_thr binds
        c = copy.deepcopy(policy_cfg)
        c["enter_long"].update({"p_up_15": cfg.p_thr, "jev_trade_ok": 0.0, "jev_failure_max": 1.0})
        d = decide({"p_up_15": p_up}, {}, c)
        return (1 if d.action == Action.ENTER_LONG else 0), {"p_up_15": p_up}
    if arm == "full":  # quant + MockJev -> default policy with matched p threshold
        c = copy.deepcopy(policy_cfg)
        c["enter_long"]["p_up_15"] = cfg.p_thr
        d = decide({"p_up_15": p_up}, jev, c)
        return (1 if d.action == Action.ENTER_LONG else 0), {"p_up_15": p_up, **jev}
    d = decide({"p_up_15": p_up}, {"trade_ok": 0.5, "failure_regime": 0.5}, policy_cfg)
    return (1 if d.action == Action.ENTER_LONG else 0), {"p_up_15": p_up}  # E: nojev


def prepare(bars: pl.DataFrame, quant) -> tuple[pl.DataFrame, object]:
    """Point-in-time features + quant probs, computed ONCE and shared by all arms."""
    feats = build_features(bars).drop_nulls(subset=list(FEATURE_COLUMNS))
    X = feats.select(["timestamp", *FEATURE_COLUMNS])
    return feats, quant.predict_up15(X).to_numpy()


def run(feats: pl.DataFrame, p_up, jev_client, arm: str, cfg: SimConfig | None = None,
        log_path: str | Path | None = None, versions: dict | None = None,
        start_ms: int | None = None) -> dict:
    """Simulate one arm with decision-time snapshots and next-bar fills.

    Each row records the decision made from bar ``t`` and the fill, if any, that
    occurred at the open of this bar from the prior decision. ``exec_ts`` is the
    scheduled execution time for the current decision and is therefore always later
    than ``ts``. A footer is emitted only after a complete run.
    """
    assert arm in ARMS, f"unknown arm {arm}"
    cfg = cfg or SimConfig()
    assert cfg.stride >= 1, "stride must be >= 1 (and > 1 is dev-only, never for gates)"
    if start_ms is not None:
        keep = feats.filter(pl.col("timestamp") >= start_ms)
        p_up = p_up[len(feats) - len(keep):]
        feats = keep
    if not len(feats) or len(p_up) != len(feats):
        raise ValueError("features and predictions must be non-empty and aligned")
    if not feats["timestamp"].is_sorted() or feats["timestamp"].n_unique() != len(feats):
        raise ValueError("feature timestamps must be strictly increasing and unique")

    ts = [int(x) for x in feats["timestamp"].to_list()]
    opens = [float(x) for x in feats["open"].to_list()]
    closes = [float(x) for x in feats["close"].to_list()]
    funds = [float(x or 0.0) for x in feats["funding_rate"].to_list()]
    rng = random.Random(cfg.seed)
    policy_cfg = load_config()
    risk = RiskKernel(cfg.risk_config_path) if cfg.risk_config_path else RiskKernel()

    pos = PositionState(0, 0.0, 0.0, 0.0, 0.0, 0)
    pending: tuple[Action, float, int] | None = None
    cum_fee = cum_fund = cum_slippage = turnover = peak = max_dd = day_pnl = 0.0
    wins = entries = exits = 0
    prev_fund: float | None = None
    prev_ts: int | None = None
    records: list[dict] = []
    log = open(log_path, "w") if log_path else None
    completed = False
    if log:
        write_header(log, arm=arm, metadata={"cfg": asdict(cfg), "versions": versions or {}})

    def equity(mark: float) -> float:
        upnl = pos.side * pos.quantity * (mark - pos.entry_price) if pos.side else 0.0
        return cfg.capital_usd + pos.realized_pnl + upnl - cum_fee - cum_fund

    try:
        order = list(range(0, len(feats), cfg.stride))
        for k, i in enumerate(order):
            now = ts[i]
            if prev_ts is not None and now // 86_400_000 != prev_ts // 86_400_000:
                risk.reset_daily()
                day_pnl = 0.0
            prev_ts = now
            held_before = pos.side != 0
            if held_before and prev_fund is not None and funds[i] != prev_fund:
                cum_fund += -pos.side * pos.quantity * closes[i] * funds[i]
            prev_fund = funds[i]

            executed = Action.NO_ACTION
            fill_decision_ts: int | None = None
            fill_px = fill_qty = fee = 0.0
            if pending is not None:
                pending_action, approved_size, origin = pending
                pending = None
                if pending_action == Action.ENTER_LONG and pos.side == 0 and approved_size > 0:
                    executed = pending_action
                    fill_decision_ts = origin
                    fill_px, slip = _fill_price(opens[i], 1, cfg)
                    fill_qty = approved_size
                    fee = _fee(fill_qty * fill_px, cfg)
                    cum_fee += fee
                    cum_slippage += slip * fill_qty
                    turnover += fill_qty * fill_px
                    pos = PositionState(1, fill_qty, fill_px, 0.0, pos.realized_pnl, 0)
                    entries += 1
                elif pending_action == Action.EXIT and pos.side != 0:
                    executed = pending_action
                    fill_decision_ts = origin
                    side = pos.side
                    fill_px, slip = _fill_price(opens[i], -side, cfg)
                    fill_qty = pos.quantity
                    fee = _fee(fill_qty * fill_px, cfg)
                    gross = side * fill_qty * (fill_px - pos.entry_price)
                    cum_fee += fee
                    cum_slippage += slip * fill_qty
                    turnover += fill_qty * fill_px
                    wins += int(gross > 0)
                    pos = PositionState(0, 0.0, 0.0, 0.0, pos.realized_pnl + gross, 0)
                    day_pnl += gross - fee
                    risk.register_fill(gross - fee, now)
                    exits += 1

            state = build_state_dict(feats.row(i, named=True)) if arm == "full" else {}
            jev = {}
            if arm == "full" and jev_client is not None:
                jev = {a.name: round(a.value, 6) for a in jev_client.ask(state, MVP_QUESTIONS)}
            want, inputs = _desired(arm, float(p_up[i]), jev, cfg, rng, policy_cfg)

            action = Action.NO_ACTION
            if want == 1 and pos.side == 0:
                action = Action.ENTER_LONG
            elif want == 0 and pos.side != 0:
                action = Action.EXIT

            nxt = order[k + 1] if k + 1 < len(order) else None
            exec_ts = None
            if action != Action.NO_ACTION and nxt is not None:
                prop = {
                    "action": action.value,
                    "confidence": 0.5,
                    "suggested_size_btc": cfg.size_btc if action == Action.ENTER_LONG else 0.0,
                }
                port = {
                    "position_btc": pos.side * pos.quantity,
                    "capital_usd": cfg.capital_usd,
                    "daily_pnl_usd": day_pnl,
                    "open_orders": 0,
                }
                mkt = {"spread_bps": cfg.spread_bps, "last_update_ms": now, "now_ms": now}
                rd = risk.evaluate(prop, port, mkt, now, closes[i])
                if rd.allowed and (action != Action.ENTER_LONG or rd.approved_size_btc > 0):
                    pending = (action, rd.approved_size_btc, now)
                    exec_ts = ts[nxt]
                else:
                    action = Action.NO_ACTION
            elif action != Action.NO_ACTION:
                action = Action.NO_ACTION

            if pos.side:
                pos.time_in_position += 1
                pos.unrealized_pnl = pos.side * pos.quantity * (closes[i] - pos.entry_price)
            eq = equity(closes[i])
            peak = max(peak, eq) if records else eq
            max_dd = max(max_dd, (peak - eq) / peak if peak else 0.0)
            rec = {
                "seq": len(records) + 1,
                "ts": now,
                "exec_ts": exec_ts,
                "state_hash": _state_hash(state) if state else None,
                "p_up": round(float(p_up[i]), 6),
                **inputs,
                "decision": action.value,
                "executed": executed.value,
                "fill_decision_ts": fill_decision_ts,
                "fill_px": round(fill_px, 4),
                "fill_qty": fill_qty,
                "fee": round(fee, 6),
                "fund_rate": funds[i],
                "pos": asdict(pos),
                "cum_fee": round(cum_fee, 4),
                "cum_fund": round(cum_fund, 4),
                "equity": round(eq, 4),
            }
            rec = write_record(log, rec) if log else {**rec, "entry_hash": entry_hash(rec)}
            records.append(rec)
        completed = True
    finally:
        if log:
            if completed:
                write_footer(log, records)
            log.close()

    net = records[-1]["equity"] - cfg.capital_usd if records else 0.0
    gross = net + cum_fee + cum_fund
    metrics = {
        "arm": arm,
        "n_bars": len(records),
        "n_entries": entries,
        "n_exits": exits,
        "n_wins": wins,
        "trade_count": entries,
        "win_rate": wins / exits if exits else None,
        "gross_pnl": round(gross, 4),
        "fees": round(cum_fee, 4),
        "slippage": round(cum_slippage, 4),
        "funding": round(cum_fund, 4),
        "net_pnl": round(net, 4),
        "return_pct": round(100 * net / cfg.capital_usd, 4),
        "max_dd_pct": round(100 * max_dd, 4),
        "avg_per_exit": round(net / exits, 4) if exits else None,
        "exposure": round(sum(1 for r in records if r["pos"]["side"]) / len(records), 4) if records else 0.0,
        "turnover": round(turnover, 4),
        "open_at_end": pos.side,
        "fee_mult": cfg.fee_mult,
        "p_thr": cfg.p_thr,
    }
    return {"metrics": metrics, "records": records}


def summarize_by_year(result: dict) -> dict:
    """Split an arm result into per-calendar-year metrics (regime robustness)."""
    from datetime import datetime, timezone

    yrs: dict[int, list[dict]] = {}
    for r in result["records"]:
        y = datetime.fromtimestamp(r["ts"] / 1000, tz=timezone.utc).year
        yrs.setdefault(y, []).append(r)
    out = {}
    for y, rs in sorted(yrs.items()):
        net = rs[-1]["equity"] - rs[0]["equity"]
        peak = dd = 0.0
        for r in rs:
            peak = max(peak, r["equity"])
            dd = max(dd, (peak - r["equity"]) / peak if peak else 0.0)
        out[y] = {"n_bars": len(rs), "net_pnl": round(net, 2),
                  "max_dd_pct": round(100 * dd, 4),
                  "n_entries": sum(1 for r in rs if r["decision"] == "ENTER_LONG"),
                  "n_exits": sum(1 for r in rs if r["decision"] == "EXIT")}
    return out
