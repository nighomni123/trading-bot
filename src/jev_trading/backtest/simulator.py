"""P7 event simulator: BAR -> STATE -> QUANT -> JEV -> POLICY -> RISK -> ORDER -> FILL -> POSITION -> PNL.

MVP conventions (documented; revisited in later P7 iterations, not silently changed):
- Next-bar execution: a signal from row t fills at open[t+1] +/- slippage.
  Same-bar close[t] fills are forbidden (lookahead) — docs/execution-semantics.md.
- Desired-position trading: each bar maps to desired +1 (long) or 0 (flat);
  NO_ACTION while holding means exit. No p_dn producer exists yet, so no shorts.
- Fixed suggested size per entry; the risk kernel clamps it (room/risk-cap/leverage).
- Funding is charged when the funding_rate print changes while holding:
  -side * qty * mark * rate (longs pay when the rate is positive).
- One JSONL record per bar behind a header line carrying component versions;
  replay = header versions + records + bars (writes are buffered in 5k batches).
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
import hashlib
import hmac
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

import polars as pl

from jev_trading.contracts import Action
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
    return hashlib.sha1(json.dumps(state, sort_keys=True).encode()).hexdigest()[:16]


def _canonical_json(value: dict) -> str:
    """Stable UTF-8 JSON with sorted keys and no whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _entry_hash(entry: dict) -> str:
    """Hash an entry's canonical content, excluding the hash field itself."""
    content = {key: value for key, value in entry.items() if key != "entry_hash"}
    return hashlib.sha256(_canonical_json(content).encode()).hexdigest()


def verify_log(path: str | Path) -> list[dict]:
    """Verify and return a simulator JSONL's ordered, untampered records.

    Design is inspired by AgentLog's append-only offsets and NautilusTrader's
    ``seq``/``entry_hash`` event-store contract; this implementation is independent.
    """
    log_path = Path(path)
    with log_path.open() as handle:
        lines = handle.readlines()
    if not lines:
        raise ValueError("event log is empty")
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid event-log header at line 1: {exc.msg}") from exc
    if not isinstance(header, dict):
        raise ValueError("event-log header must be a JSON object")
    if header.get("type") != "header" or header.get("schema_version") != 2:
        raise ValueError("event-log header is missing schema_version 2")

    records: list[dict] = []
    expected_seq = 1
    for line_no, raw in enumerate(lines[1:], start=2):
        line = raw.rstrip("\n")
        if not line:
            raise ValueError(f"blank event-log line at {line_no}")
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid event-log JSON at line {line_no}: {exc.msg}") from exc
        if not isinstance(entry, dict):
            raise ValueError(f"event-log entry must be a JSON object at line {line_no}")
        seq = entry.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq != expected_seq:
            raise ValueError(
                f"event-log sequence gap at line {line_no}: expected {expected_seq}, got {seq}"
            )
        try:
            expected = _entry_hash(entry)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid event-log entry content at line {line_no}: {exc}") from exc
        if not isinstance(entry.get("entry_hash"), str) or not hmac.compare_digest(
            entry["entry_hash"], expected
        ):
            raise ValueError(f"event-log entry hash mismatch at line {line_no}")
        records.append(entry)
        expected_seq += 1
    return records


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
    """Simulate one arm over precomputed `feats`/`p_up` (see prepare()).

    `start_ms` selects the run window; earlier history fed features only — every
    iterated row's features use bars 0..t (point-in-time, per the P2 no-lookahead
    test). `jev_client` needs `.ask(state, questions)` (full arm; None otherwise).
    Returns {"metrics", "records"} and optionally writes JSONL (header + one
    record per iterated bar) to `log_path`.
    """
    assert arm in ARMS, f"unknown arm {arm}"
    cfg = cfg or SimConfig()
    assert cfg.stride >= 1, "stride must be >= 1 (and > 1 is dev-only, never for gates)"
    rng = random.Random(cfg.seed)
    policy_cfg = load_config()
    risk = RiskKernel(cfg.risk_config_path) if cfg.risk_config_path else RiskKernel()

    if start_ms is not None:
        keep = feats.filter(pl.col("timestamp") >= start_ms)
        p_up = p_up[len(feats) - len(keep):]  # ts strictly increasing: drops head rows only
        feats = keep
    ts = feats["timestamp"].to_list()
    opens = feats["open"].to_list()
    closes = feats["close"].to_list()
    funds = feats["funding_rate"].to_list()

    pos = PositionState(0, 0.0, 0.0, 0.0, 0.0, 0)
    cum_fee = cum_fund = peak = max_dd = day_pnl = 0.0
    wins = entries = exits = 0
    prev_fund = funds[0]
    records: list[dict] = []
    buf: list[str] = []  # log write buffer (5k-batch flushes)
    log = open(log_path, "w") if log_path else None
    if log:
        log.write(json.dumps({"type": "header", "schema_version": 2, "arm": arm, "cfg": asdict(cfg),
                              "versions": versions or {}}) + "\n")

    def equity(mark: float) -> float:
        upnl = pos.side * pos.quantity * (mark - pos.entry_price) if pos.side else 0.0
        return cfg.capital_usd + pos.realized_pnl + upnl - cum_fee - cum_fund

    try:
        order = range(0, len(feats), max(1, cfg.stride))
        prev_ts: int | None = None
        for k, i in enumerate(order):
            nxt = order[k + 1] if k + 1 < len(order) else None
            if funds[i] != prev_fund and pos.side:  # funding print changed while holding
                cum_fund += -pos.side * pos.quantity * closes[i] * funds[i]
            prev_fund = funds[i]
            if prev_ts is not None and ts[i] // 86_400_000 != prev_ts // 86_400_000:
                risk.reset_daily()
                day_pnl = 0.0
            prev_ts = ts[i]

            state = build_state_dict(feats.row(i, named=True)) if arm == "full" else {}
            jev = {}
            if arm == "full" and jev_client is not None:
                jev = {a.name: round(a.value, 6) for a in jev_client.ask(state, MVP_QUESTIONS)}
            want, inputs = _desired(arm, float(p_up[i]), jev, cfg, rng, policy_cfg)

            action, fill_px, fill_qty, fee = Action.NO_ACTION, 0.0, 0.0, 0.0
            if want == 1 and pos.side == 0:
                action = Action.ENTER_LONG
            elif want == 0 and pos.side != 0:
                action = Action.EXIT
            if action != Action.NO_ACTION and nxt is not None:
                prop = {"action": action.value, "confidence": 0.5,
                        "suggested_size_btc": cfg.size_btc if action == Action.ENTER_LONG else 0.0}
                port = {"position_btc": pos.side * pos.quantity, "capital_usd": cfg.capital_usd,
                        "daily_pnl_usd": day_pnl, "open_orders": 0}
                mkt = {"spread_bps": cfg.spread_bps, "last_update_ms": ts[i], "now_ms": ts[i]}
                rd = risk.evaluate(prop, port, mkt, ts[i], closes[i])
                if rd.allowed:
                    fill_px, slip = _fill_price(opens[nxt], 1 if action == Action.ENTER_LONG else -1, cfg)
                    if action == Action.ENTER_LONG:
                        fill_qty = rd.approved_size_btc
                        fee = _fee(fill_qty * fill_px, cfg)
                        pos = PositionState(1, fill_qty, fill_px, 0.0, pos.realized_pnl, 0)
                        entries += 1
                    else:
                        fill_qty = pos.quantity
                        gross = pos.side * fill_qty * (fill_px - pos.entry_price)
                        fee = _fee(fill_qty * fill_px, cfg)
                        wins += gross > 0
                        pos = PositionState(0, 0.0, 0.0, 0.0, pos.realized_pnl + gross, 0)
                        day_pnl += gross - fee
                        risk.register_fill(gross - fee, ts[nxt])
                        exits += 1
                    cum_fee += fee
                action = action if rd.allowed else Action.NO_ACTION
            elif action != Action.NO_ACTION:
                action = Action.NO_ACTION  # last iterated bar: nothing ahead to fill at

            if pos.side:
                pos.time_in_position += 1
                pos.unrealized_pnl = pos.side * pos.quantity * (closes[i] - pos.entry_price)
            eq = equity(closes[i])
            peak = max(peak, eq) if records else eq
            max_dd = max(max_dd, (peak - eq) / peak if peak else 0.0)
            rec = {"ts": ts[i], "exec_ts": ts[nxt] if action != Action.NO_ACTION else None,
                   "state_hash": _state_hash(state) if state else None,
                   "p_up": round(float(p_up[i]), 6), **inputs,
                   "decision": action.value, "fill_px": round(fill_px, 4), "fill_qty": fill_qty,
                   "fee": round(fee, 6), "fund_rate": funds[i],
                   "pos": asdict(pos), "cum_fee": round(cum_fee, 4), "cum_fund": round(cum_fund, 4),
                   "equity": round(eq, 4)}
            rec["seq"] = len(records) + 1
            rec["entry_hash"] = _entry_hash(rec)
            records.append(rec)
            if log:
                buf.append(json.dumps(rec))
                if len(buf) >= 5000:
                    log.write("\n".join(buf) + "\n")
                    buf.clear()
    finally:
        if log:
            if buf:
                log.write("\n".join(buf) + "\n")
            log.close()

    net = records[-1]["equity"] - cfg.capital_usd if records else 0.0
    metrics = {"arm": arm, "n_bars": len(records), "n_entries": entries, "n_exits": exits,
               "win_rate": wins / exits if exits else None,
               "fees": round(cum_fee, 4), "funding": round(cum_fund, 4),
               "net_pnl": round(net, 4), "return_pct": round(100 * net / cfg.capital_usd, 4),
               "max_dd_pct": round(100 * max_dd, 4),
               "avg_per_exit": round(net / exits, 4) if exits else None,
               "exposure": round(sum(1 for r in records if r["pos"]["side"]) / len(records), 4) if records else 0.0,
               "open_at_end": pos.side,
               "fee_mult": cfg.fee_mult, "p_thr": cfg.p_thr}
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
