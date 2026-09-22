"""P7 simulator contract tests: synthetic bars only, stub quant/jev, no network."""
from __future__ import annotations

import json

import polars as pl
import pytest

from jev_trading.backtest.simulator import ARMS, SimConfig, prepare, run, summarize_by_year, verify_log

T0 = 1672531200000  # 2023-01-01T00:00:00Z
MIN = 60_000
N = 2000  # > 1440-bar warmup so drop_nulls leaves rows


class StubQuant:
    """predict_up15 returning a per-row pattern over the feature frame."""

    def __init__(self, fn):
        self._fn = fn

    def predict_up15(self, X: pl.DataFrame) -> pl.Series:
        return pl.Series("p_up15", [self._fn(i) for i in range(len(X))])


class StubJev:
    def __init__(self, answers: dict):
        self._a = answers

    def ask(self, state, questions):
        from jev_trading.jev.client import Answer

        return [Answer(name=q.name, value=self._a.get(q.name, 0.5)) for q in questions]


def make_bars(n: int = N, close: float = 100.0, fund: float = 0.0001,
              gap_at: int | None = None) -> pl.DataFrame:
    """Flat 100s; open[t+1] = close[t] + 0.5 so next-bar fills differ from same-bar close."""
    closes = [close] * n
    opens = [close - 0.5] + [c + 0.5 for c in closes[:-1]]
    if gap_at is not None:
        opens[gap_at] += 5.0
    return pl.DataFrame(
        {
            "timestamp": [T0 + i * MIN for i in range(n)],
            "open": opens,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [10.0] * n,
            "funding_rate": [fund] * n,
            "open_interest": [1000.0] * n,
        }
    )


def spike_quant(at: set[int], hi: float = 0.9, lo: float = 0.1) -> StubQuant:
    return StubQuant(lambda i: hi if i in at else lo)


def sim(bars: pl.DataFrame, q, *args, **kwargs) -> dict:
    """prepare() + run(): same shared-features path the ablation script uses."""
    feats, p_up = prepare(bars, q)
    return run(feats, p_up, *args, **kwargs)


BIG = SimConfig(p_thr=0.5, capital_usd=100_000)  # risk-cap >> 0.01 so approved == suggested


def test_fill_at_next_open_plus_slippage():
    bars = make_bars()
    recs = sim(bars, spike_quant({10}, hi=0.9), None, "threshold", BIG)["records"]
    fills = [r for r in recs if r["decision"] == "ENTER_LONG"]
    assert len(fills) == 1
    f = fills[0]
    assert f["exec_ts"] == f["ts"] + MIN  # next bar, never same bar
    assert f["fill_px"] == pytest.approx((100.0 + 0.5) * 1.0002)  # open[t+1] + slippage
    assert f["fill_px"] != pytest.approx(100.0)  # not the signal-bar close
    assert f["fill_qty"] == pytest.approx(0.01)  # suggested size, risk-clamped room allows


def test_shifted_signal_changes_fill():
    bars = make_bars()
    probe = sim(bars, spike_quant({10}), None, "threshold", BIG)["records"]
    fill_bar = (next(r for r in probe if r["decision"] == "ENTER_LONG")["exec_ts"] - T0) // MIN
    gapped = make_bars(gap_at=fill_bar + 1)  # jump lands on the delayed signal's fill bar
    early = sim(bars, spike_quant({10}), None, "threshold", BIG)["records"]
    late = sim(gapped, spike_quant({11}), None, "threshold", BIG)["records"]
    fe = next(r for r in early if r["decision"] == "ENTER_LONG")
    fl = next(r for r in late if r["decision"] == "ENTER_LONG")
    assert fe["fill_px"] != fl["fill_px"]  # one-bar delay lands on the gapped open


def test_last_bar_signal_never_fills_same_bar():
    bars = make_bars()
    n_feat = sim(bars, spike_quant(set()), None, "threshold", BIG)["metrics"]["n_bars"]
    out = sim(bars, spike_quant({n_feat - 1}, hi=0.9), None, "threshold", BIG)
    assert all(r["decision"] == "NO_ACTION" for r in out["records"])


def test_exit_realizes_pnl_net_of_fees():
    bars = make_bars()  # flat: every hold is exactly one bar, exit at next open
    out = sim(bars, spike_quant({10}, hi=0.9), None, "threshold", BIG)
    m = out["metrics"]
    assert (m["n_entries"], m["n_exits"]) == (1, 1)
    entry = next(r for r in out["records"] if r["decision"] == "ENTER_LONG")
    exitr = next(r for r in out["records"] if r["decision"] == "EXIT")
    gross = 0.01 * (exitr["fill_px"] - entry["fill_px"])
    assert m["fees"] == pytest.approx(2 * 0.01 * 100.5 * 1.0002 * 0.0005, abs=1e-4)
    assert m["net_pnl"] == pytest.approx(gross - m["fees"], abs=0.05)


def test_funding_charged_on_print_change_while_holding():
    bars = make_bars()
    funds = bars["funding_rate"].to_list()
    for k in range(len(funds)):  # alternating prints: any multi-bar hold crosses transitions
        funds[k] = 0.0002 if (k // 10) % 2 else 0.0001
    bars = bars.with_columns(pl.Series("funding_rate", funds))
    q = StubQuant(lambda i: 0.9 if 5 <= i < 45 else 0.1)  # enter early, hold ~40 bars
    out = sim(bars, q, None, "threshold", BIG)
    assert out["metrics"]["n_exits"] == 1
    assert -0.01 < out["metrics"]["funding"] < 0  # long pays positive funding, dust-sized here


def test_jsonl_log_replayable(tmp_path):
    p = tmp_path / "arm.jsonl"
    out = sim(make_bars(), spike_quant({10, 30}, hi=0.9), None, "threshold",
              SimConfig(p_thr=0.5), log_path=p, versions={"data": "test"})
    lines = p.read_text().splitlines()
    header = json.loads(lines[0])
    assert header["type"] == "header" and header["schema_version"] == 2 and header["arm"] == "threshold"
    assert header["versions"] == {"data": "test"}
    assert len(lines) - 1 == len(out["records"]) == out["metrics"]["n_bars"]
    body = [json.loads(line) for line in lines[1:]]
    assert [record["seq"] for record in body] == list(range(1, len(body) + 1))
    assert all(len(record["entry_hash"]) == 64 for record in body)
    assert verify_log(p) == body
    for record in body:
        assert {"ts", "exec_ts", "state_hash", "p_up", "decision", "pos", "equity"} <= set(record)
        assert record["state_hash"] is None  # threshold arm consumes p_up only, no market state
    assert body[-1]["equity"] == pytest.approx(out["metrics"]["net_pnl"] + 10_000.0, abs=0.01)


def test_jsonl_verifier_rejects_tampering_and_sequence_gaps(tmp_path):
    p = tmp_path / "arm.jsonl"
    sim(make_bars(), spike_quant({10}), None, "threshold",
        SimConfig(p_thr=0.5), log_path=p, versions={"data": "test"})
    lines = p.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["equity"] += 1.0
    lines[1] = json.dumps(tampered)
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="entry hash mismatch"):
        verify_log(p)

    sim(make_bars(), spike_quant({10}), None, "threshold",
        SimConfig(p_thr=0.5), log_path=p)
    lines = p.read_text().splitlines()
    lines.pop(1)
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="sequence gap"):
        verify_log(p)


def test_random_arm_seeded_reproducible():
    bars = make_bars()
    a = sim(bars, StubQuant(lambda i: 0.1), None, "random", SimConfig(seed=7))
    b = sim(bars, StubQuant(lambda i: 0.1), None, "random", SimConfig(seed=7))
    assert [r["decision"] for r in a["records"]] == [r["decision"] for r in b["records"]]
    c = sim(bars, StubQuant(lambda i: 0.1), None, "random", SimConfig(seed=8))
    assert [r["decision"] for r in c["records"]] != [r["decision"] for r in a["records"]]


def test_simulator_uses_custom_risk_config(tmp_path):
    risk_path = tmp_path / "risk.json"
    risk_path.write_text(json.dumps({
        "max_position_btc": 0.1,
        "max_leverage": 2.0,
        "daily_loss_limit_usd": 50.0,
        "trade_loss_limit_usd": 20.0,
        "max_spread_bps": 5.0,
        "stale_data_ms": 15_000,
        "max_orders_per_min": 12,
        "risk_per_trade_pct": 1.0,
        "cooldown_ms": 60 * MIN,
        "max_drawdown_pct": 0.0,
    }))
    out = sim(make_bars(), spike_quant({10, 20}, hi=0.9), None, "threshold",
              SimConfig(p_thr=0.5, risk_config_path=str(risk_path)))
    assert out["metrics"]["n_entries"] == 1


def test_nojev_arm_stays_flat_by_design():
    out = sim(make_bars(), spike_quant(set(range(531)), hi=0.9), None, "nojev",
              SimConfig(p_thr=0.5))
    assert out["metrics"]["n_entries"] == 0  # neutral Jev never clears 0.75/0.35 gates


def test_full_arm_enters_on_passing_mock_answers():
    jev = StubJev({"trade_ok": 0.9, "failure_regime": 0.1})
    out = sim(make_bars(), spike_quant({10}, hi=0.9), jev, "full", SimConfig(p_thr=0.5))
    assert out["metrics"]["n_entries"] == 1
    rec = next(r for r in out["records"] if r["decision"] == "ENTER_LONG")
    assert rec["trade_ok"] == 0.9 and rec["failure_regime"] == 0.1
    assert len(rec["state_hash"]) == 16  # full arm logs the state behind its Jev call


def test_summarize_by_year_splits():
    out = sim(make_bars(), spike_quant({10}, hi=0.9), None, "threshold", SimConfig(p_thr=0.5))
    by = summarize_by_year(out)
    assert list(by) == [2023] and by[2023]["n_entries"] == 1
    assert by[2023]["net_pnl"] == pytest.approx(out["metrics"]["net_pnl"], abs=1.0)


def test_stride_dev_samples_loop():
    bars = make_bars()
    n_full = sim(bars, spike_quant(set()), None, "threshold", BIG)["metrics"]["n_bars"]
    out = sim(bars, spike_quant({5}, hi=0.9), None, "threshold",
              SimConfig(p_thr=0.5, capital_usd=100_000, stride=5))
    assert out["metrics"]["n_bars"] == (n_full + 4) // 5
    feats, _ = prepare(bars, spike_quant(set()))
    f = next(r for r in out["records"] if r["decision"] == "ENTER_LONG")
    assert f["exec_ts"] == feats["timestamp"][10]  # next iterated row: still future, never same-bar
    with pytest.raises(AssertionError):
        sim(bars, spike_quant(set()), None, "threshold", SimConfig(stride=0))


def test_arms_registered():
    assert ARMS == ("random", "threshold", "policy", "full", "nojev")
