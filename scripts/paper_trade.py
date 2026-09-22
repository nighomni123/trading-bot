#!/usr/bin/env python3
"""P8 shadow paper trader: live BTCUSDT perp bars through the full decision pipeline.

  Binance -> [state] -> [quant Lgbm] -> [jev mock] -> [policy] -> [risk] -> [paper exec] -> JSONL

Zero real money: paper fills at next-open (lookahead-free per docs/execution-semantics.md),
tracked in events/. Validates the live pipeline end-to-end (data latency, feature warmup,
model inference, risk enforcement, event logging) even though the P7 gate verdict is FAIL.

Usage:
    .venv/bin/python scripts/paper_trade.py [--capital 10000] [--size-btc 0.01]
        [--duration 3600] [--p-thr 0.40] [--fee-mult 1.0]

Press Ctrl+C to stop. Final metrics + log path printed on exit.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from jev_trading.contracts import Action, BAR_COLUMNS
from jev_trading.data.fetch import fetch_bars, fetch_klines
from jev_trading.execution import PaperTrader, PaperCosts
from jev_trading.jev.mock import MockJev
from jev_trading.jev.questions import MVP_QUESTIONS
from jev_trading.policy.engine import decide, load_config
from jev_trading.quant.model import load_models
from jev_trading.risk.kernel import RiskKernel
from jev_trading.state.features import (
    FEATURE_COLUMNS,
    build_features,
    build_state_dict,
)

MINUTE_MS = 60_000
DAY_MS = 86_400_000
WARMUP_BARS = 3000


def _aligned_minute_ms() -> int:
    now = int(time.time() * 1000)
    return now - (now % MINUTE_MS)


def _utc_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M:%S")


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--size-btc", type=float, default=0.01)
    ap.add_argument("--duration", type=int, default=0, help="run seconds (0 = forever)")
    ap.add_argument("--p-thr", type=float, default=0.40)
    ap.add_argument("--fee-mult", type=float, default=1.0)
    ap.add_argument("--poll-interval", type=float, default=15.0)
    ap.add_argument("--window", type=int, default=WARMUP_BARS)
    args = ap.parse_args(argv)

    # -- components -------------------------------------------------------
    print("loading quant model...")
    quant = load_models("models/")
    policy_cfg = load_config()
    policy_cfg["enter_long"]["p_up_15"] = args.p_thr
    jev = MockJev()
    risk = RiskKernel()
    costs = PaperCosts(fee_mult=args.fee_mult)
    trader = PaperTrader(capital_usd=args.capital, risk=risk, costs=costs, size_btc=args.size_btc)

    # -- event log --------------------------------------------------------
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = Path("events") / f"shadow_{ts_tag}.jsonl"
    trader.open_log(log_path)
    print(f"event log: {log_path}")

    # -- warm buffer (fresh from Binance) -------------------------------
    end_ms = _aligned_minute_ms()
    start_ms = end_ms - args.window * MINUTE_MS
    print(f"fetching warm buffer [{start_ms}, {end_ms}) ({args.window} bars)...")
    t0 = time.time()
    bars = fetch_bars(start_ms, end_ms)
    dt = time.time() - t0
    if bars.is_empty():
        print("ERROR: warm buffer fetch returned empty — no network or Binance down")
        return 1
    print(f"warm buffer: {bars.height} bars in {dt:.1f}s "
          f"[{_utc_str(int(bars['timestamp'][0]))} .. {_utc_str(int(bars['timestamp'][-1]))}]")

    # -- initial features -------------------------------------------------
    feats = build_features(bars).drop_nulls(subset=list(FEATURE_COLUMNS))
    if feats.is_empty():
        print("ERROR: no valid feature rows after warmup")
        return 1
    print(f"valid feature rows: {feats.height} (first usable ts: {_utc_str(int(feats['timestamp'][0]))})")

    last_ts = int(feats["timestamp"][-1])
    last_bar_ts = last_ts

    # -- signal handling -------------------------------------------------
    running = True

    def _stop(*_):
        nonlocal running
        running = False
        print("\nstopping after current bar...", flush=True)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    # -- live loop --------------------------------------------------------
    print(f"\n=== Live paper trading started  "
          f"capital=${args.capital:.0f} size={args.size_btc}BTC "
          f"p_thr={args.p_thr} fee_mult={args.fee_mult} ===")
    print(f"poll every {args.poll_interval}s | press Ctrl+C to stop\n")

    bar_count = 0
    start_time = time.time()

    while running:
        if args.duration > 0 and time.time() - start_time > args.duration:
            break

        t_fetch = time.time()
        try:
            end_ms = _aligned_minute_ms()
            # fetch last 3 closed minutes; end_ms is start of current minute so
            # the latest returned bar is the last *completed* 1m candle
            fetch_start = end_ms - 3 * MINUTE_MS
            klines = fetch_klines(fetch_start, end_ms)
            if klines.is_empty():
                await asyncio.sleep(args.poll_interval)
                continue

            latest = klines.tail(1)
            latest_ts = int(latest["timestamp"][0])

            if latest_ts <= last_bar_ts:
                await asyncio.sleep(args.poll_interval)
                continue

            last_bar_ts = latest_ts
            fetch_ms = int((time.time() - t_fetch) * 1000)

            # forward-fill last known funding/OI into the new bar (funding/OI
            # change at most once per 8h / per day; ponytail: a live ticker would
            # poll fetch_funding periodically — forward-fill is sufficient for shadow)
            last_fund = float(bars["funding_rate"][-1])
            last_oi = float(bars["open_interest"][-1])
            new_bar = latest.with_columns(
                pl.lit(last_fund).alias("funding_rate"),
                pl.lit(last_oi).alias("open_interest"),
            )

            # roll buffer (drop oldest to maintain window)
            if bars.height >= args.window:
                bars = pl.concat([bars.slice(1, bars.height - 1), new_bar], how="diagonal_relaxed")
            else:
                bars = pl.concat([bars, new_bar], how="diagonal_relaxed")

            # rebuild features on rolling window
            feats = build_features(bars).drop_nulls(subset=list(FEATURE_COLUMNS))
            if feats.is_empty():
                print(f"  [{_utc_str(latest_ts)}] WARN: no valid features after warmup, skipping")
                await asyncio.sleep(args.poll_interval)
                continue

            row = feats.row(-1, named=True)
            bar_dict = {c: row[c] for c in BAR_COLUMNS}
            state = build_state_dict(row)
            state_hash = _sha256_hex(json.dumps(state, sort_keys=True))

            # quant
            X = feats.select(["timestamp", *FEATURE_COLUMNS]).tail(1)
            p_up = float(quant.predict_up15(X).to_numpy()[0])

            # jev
            answers = jev.ask(state, MVP_QUESTIONS)
            jev_dict = {a.name: a.value for a in answers}

            # policy
            proposal = decide({"p_up_15": p_up, **state}, jev_dict, policy_cfg)

            # risk
            port = {
                "position_btc": trader.position.side * trader.position.quantity,
                "capital_usd": trader.capital,
                "daily_pnl_usd": trader.daily_pnl,
                "open_orders": 0,
            }
            mkt = {
                "spread_bps": 1.0,
                "last_update_ms": latest_ts,
                "now_ms": latest_ts,
            }
            rd = risk.evaluate(proposal, port, mkt, latest_ts, float(row["close"]))

            # paper execution (handles pending fill at open + buffers new decision)
            latency = (time.time() - t_fetch) * 1000
            record = trader.on_bar(
                bar_dict, p_up=p_up, jev=jev_dict, proposal=proposal,
                risk_decision=rd, state_hash=state_hash, latency_ms=latency,
            )
            bar_count += 1

            # -- diagnostics -------------------------------------------------
            m = trader.metrics
            pos = m["position"]
            tag = "FILL" if record["executed"] != "NO_ACTION" else "    "
            fund_str = f"fund={last_fund:+.5f}"
            line = (
                f"[{_utc_str(latest_ts)}] {tag} {record['decision']:10s}→{record['executed']:10s} "
                f"p_up={p_up:.4f} ok={jev_dict.get('trade_ok',0):.2f} "
                f"→{rd.reason[:24]:24s} "
                f"| pos={pos['side']:+d}x{pos['quantity']:.4f}BTC "
                f"equity=${m['equity']:.2f} PnL=${m['net_pnl']:+.2f} "
                f"fees=${m['cum_fee']:.2f} lat={latency:.0f}ms fetch={fetch_ms}ms {fund_str}"
            )
            print(line)

            if rd.allowed and proposal.action != Action.NO_ACTION and record["executed"] == "NO_ACTION":
                pending_str = record.get("pending")
                if pending_str:
                    print(f"    → buffered: {pending_str} ({rd.approved_size_btc:.4f}BTC) for next-open fill")

        except Exception as e:
            print(f"\nERROR on bar: {e}")
            traceback.print_exc()
            await asyncio.sleep(args.poll_interval)

        # poll cadence
        elapsed = time.time() - t_fetch
        sleep_for = max(0.5, args.poll_interval - elapsed)
        await asyncio.sleep(sleep_for)

    trader.close_log()
    print(f"\n=== Stopped after {bar_count} bars ({time.time() - start_time:.0f}s) ===")
    print(json.dumps(trader.metrics, indent=2, default=str))
    print(f"Log: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
