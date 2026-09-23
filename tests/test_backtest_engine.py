"""Adapter tests for pluggable backtest engine."""
from jev_trading.backtest.base import BacktestEngine, BacktestResult, Trade, DecisionEvent
from jev_trading.backtest.local import LocalBacktestEngine
from jev_trading.backtest.lean import LeanBacktestEngine
from jev_trading.backtest.data_adapter import to_lean_csv
from jev_trading.backtest.comparison import compare_backtests, reconcile_trades


def test_local_implements_engine():
    assert issubclass(LocalBacktestEngine, BacktestEngine)


def test_lean_implements_engine():
    assert issubclass(LeanBacktestEngine, BacktestEngine)


def test_result_normalization():
    r = BacktestResult(
        engine="test", engine_version="v1", experiment_id="E1",
        start_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        end_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        initial_capital=1000, final_equity=1100, net_pnl=100,
        gross_pnl=100, fees=0, slippage=0, return_pct=0.1,
        max_drawdown=0.05, trade_count=1, winning_trades=1,
        losing_trades=0, win_rate=1.0, profit_factor=1.0,
    )
    assert r.net_pnl == 100


def test_data_conversion():
    import polars as pl
    df = pl.DataFrame({"timestamp":[1],"open":[100],"high":[101],"low":[99],"close":[100],"volume":[10],"funding_rate":[0.01],"open_interest":[1]})
    lean_df = to_lean_csv(df)
    assert "Time" in lean_df.columns


def test_reconciliation():
    t1 = Trade(trade_id="T1", engine="local")
    t2 = Trade(trade_id="T1", engine="lean")
    rec = reconcile_trades([t1], [t2])
    assert "common" in rec
