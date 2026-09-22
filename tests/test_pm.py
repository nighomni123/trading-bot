"""Unit tests for the prediction-market feed — network fully disabled (only `_get` mocked).

Mirrors tests/test_data.py: the single touchpoint `pm._get` is swapped for a
router keyed on URL substrings; `requests.get` is nailed to raise. Both the
Kalshi path (unreachable here) and the Polymarket path are exercised via mocks
using the real response shapes (see AGENTS.md research).
"""
from __future__ import annotations

import json

import polars as pl
import pytest

from jev_trading.contracts import PM_BAR_COLUMNS, PM_COLUMNS
from jev_trading.data import pm as M

MS_DAY = 86_400_000


def _j(payload) -> bytes:
    return json.dumps(payload).encode()


def _route(monkeypatch, handlers: dict) -> None:
    """handlers: url-substring -> bytes | Exception | callable(url, params) -> bytes|raises."""

    def fake_get(url: str, params: dict | None = None, headers=None) -> bytes:
        for key, fn in handlers.items():
            if key in url:
                out = fn(url, params) if callable(fn) else fn
                if isinstance(out, Exception):
                    raise out
                return out
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(M, "_get", fake_get)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("network disabled in tests")
    monkeypatch.setattr(M.requests, "get", boom)


# --- shared helpers --------------------------------------------------------

_GAMMA_MKT = {
    "id": "559651",
    "question": "Will BTC be above $110,000 by 31 Dec 2025?",
    "conditionId": "0xPOLYCOND12345678901234567890123456789012",
    "slug": "btc-above-110k",
    "outcomePrices": '["0.425", "0.575"]',
    # Polymarket token ids are bare numeric strings (no 0x); CLOB /book & /prices-history use them.
    "clobTokenIds": '["32338220190071351435772801779725302244575775216413325951443816017994629993401"]',
    "bestBid": "0.42",
    "bestAsk": "0.43",
    "lastTradePrice": "0.425",
    "spread": "0.001",
    "volume": "13814385.37",
    "liquidity": "503466.9",
    "liquidityNum": "503466.9",
    "openInterest": "3955881.3",
    "volume24hr": "91374.5",
    "endDate": "2026-01-01T04:59:00Z",
    "updatedAt": "2026-09-22T16:21:53.131Z",
    "active": True,
    "closed": False,
}
_GAMMA_BOOK = {
    "market": "0xPOLYCOND12345678901234567890123456789012",
    "asset_id": "32338220190071351435772801779725302244575775216413325951443816017994629993401",
    "timestamp": "1790095100295",
    "bids": [{"price": "0.42", "size": "100"}, {"price": "0.41", "size": "200"}],
    "asks": [{"price": "0.43", "size": "150"}, {"price": "0.44", "size": "250"}],
    "last_trade_price": "0.425",
}
_KALSHI_MKT = {
    "ticker": "KXFUTUREBTC-25DEC31",
    "event_ticker": "KXFUTUREBTC",
    "title": "Will BTC be above $110k by 31 Dec 2025?",
    "yes_sub_title": "Yes",
    "no_sub_title": "No",
    "yes_bid_dollars": "0.4200",
    "yes_ask_dollars": "0.4400",
    "last_price_dollars": "0.4300",
    "yes_bid_size_fp": "250.00",
    "yes_ask_size_fp": "300.00",
    "volume_fp": "5000.00",
    "volume_24h_fp": "420.00",
    "open_interest_fp": "12000.00",
    "notional_value_dollars": "1.0000",
    "close_time": "2025-12-31T16:00:00Z",
    "updated_time": "2026-09-22T16:21:53Z",
    "status": "active",
}


# --- scalar helpers --------------------------------------------------------

def test_fp_parses_fixed_point_and_strings():
    assert M._fp("0.425") == 0.425
    assert M._fp("0.50") == 0.5
    assert M._fp(0.5) == 0.5
    assert M._fp("") is None
    assert M._fp(None) is None
    assert M._fp("junk") is None


def test_scale_applies_notional_and_keeps_none():
    assert M._scale(M._fp("5.00"), 2.0) == 10.0
    assert M._scale(None, 2.0) is None


def test_iso_ms_parses_iso_and_unix():
    assert M._iso_ms("2026-09-22T16:21:53.131Z") == 1790094113131
    assert M._iso_ms(1700000000) == 1700000000000  # seconds -> ms
    assert M._iso_ms(1700000000000) == 1700000000000
    assert M._iso_ms("") is None


# --- Polymarket snapshot ---------------------------------------------------

def test_polymarket_snapshot_schema(monkeypatch):
    _route(monkeypatch, {
        "gamma-api.polymarket.com/markets/559651": lambda _u, _p: _j(_GAMMA_MKT),
        "clob.polymarket.com/book": lambda _u, _p: _j(_GAMMA_BOOK),
    })
    df = M.fetch_pm_snapshot(platforms=["polymarket"],
                             market_ids={"polymarket": ["559651"]})
    assert df.columns == list(PM_COLUMNS)
    assert df.height == 1
    r = df.row(0, named=True)
    assert r["platform"] == "polymarket"
    assert r["market_id"] == "559651"
    assert r["question"].startswith("Will BTC")
    assert r["yes_bid"] == pytest.approx(0.42)
    assert r["yes_ask"] == pytest.approx(0.43)
    assert r["last_price"] == pytest.approx(0.425)
    assert r["spread"] == pytest.approx(0.01)
    assert r["mid_price"] == pytest.approx(0.425)
    assert r["best_bid_size"] == pytest.approx(100.0)
    assert r["best_ask_size"] == pytest.approx(150.0)
    assert r["depth_bids"] == pytest.approx(300.0)  # 100 + 200
    assert r["depth_asks"] == pytest.approx(400.0)  # 150 + 250
    assert r["close_date"] == 1767243540000  # 2026-01-01T04:59:00Z
    assert r["status"] == "open"
    assert df.schema["timestamp"] == pl.Int64
    for c in ("yes_bid", "yes_ask", "last_price", "spread", "mid_price",
              "best_bid_size", "best_ask_size", "depth_bids", "depth_asks",
              "liquidity", "volume_24h", "volume_total", "open_interest"):
        assert df.schema[c] == pl.Float64


def test_polymarket_price_history_bars(monkeypatch):
    _route(monkeypatch, {
        "gamma-api.polymarket.com/markets/559651": lambda _u, _p: _j(_GAMMA_MKT),
        "clob.polymarket.com/prices-history": lambda _u, _p: _j({
            "history": [
                {"t": 1790000000, "p": 0.42},
                {"t": 1790000060, "p": 0.43},
                {"t": 1790000120, "p": 0.44},
            ]
        }),
    })
    df = M.fetch_pm_bars(
        1790000000000, 1790000200000,
        platforms=["polymarket"],
        market_ids={"polymarket": ["559651"]},
        interval="1m",
    )
    assert df.columns == list(PM_BAR_COLUMNS)
    assert df.height == 3
    ts = df["timestamp"].to_list()
    assert all(b > a for a, b in zip(ts, ts[1:])), "strictly increasing"
    assert df["platform"][0] == "polymarket"
    assert df["market_id"][0] == "559651"
    assert df["close"].to_list() == [0.42, 0.43, 0.44]
    # OHLC with no per-candle open/high/low collapses to close (single trade price)
    assert df["open"][0] == pytest.approx(0.42)


# --- Kalshi snapshot -------------------------------------------------------

def test_kalshi_snapshot_schema(monkeypatch):
    _route(monkeypatch, {
        "kalshi.com": lambda _u, _p: _j(_KALSHI_MKT),
    })
    df = M.fetch_pm_snapshot(
        platforms=["kalshi"],
        market_ids={"kalshi": ["KXFUTUREBTC-25DEC31"]},
    )
    assert df.columns == list(PM_COLUMNS)
    assert df.height == 1
    r = df.row(0, named=True)
    assert r["platform"] == "kalshi"
    assert r["market_id"] == "KXFUTUREBTC-25DEC31"
    assert r["yes_bid"] == pytest.approx(0.42)
    assert r["yes_ask"] == pytest.approx(0.44)
    assert r["last_price"] == pytest.approx(0.43)
    assert r["spread"] == pytest.approx(0.02)
    assert r["mid_price"] == pytest.approx(0.43)
    assert r["volume_total"] == pytest.approx(5000.0)  # contracts * notional(1) = 5000
    assert r["open_interest"] == pytest.approx(12000.0)
    assert r["close_date"] == 1767196800000  # 2025-12-31T16:00:00Z
    assert r["status"] == "active"


def test_kalshi_candlesticks_to_bars(monkeypatch):
    _route(monkeypatch, {
        "gamma-api.polymarket.com": lambda _u, _p: _j(_GAMMA_MKT),  # for metadata only
        "kalshi.com": lambda _u, _p: _j({
            "ticker": "KXFUTUREBTC-25DEC31",
            "candlesticks": [
                {"end_period_ts": 1790000060,
                 "price": {"open_dollars": "0.42", "high_dollars": "0.44",
                           "low_dollars": "0.41", "close_dollars": "0.43"},
                 "volume_fp": "120.00", "open_interest_fp": "4000.00"},
                {"end_period_ts": 1790000120,
                 "price": {"open_dollars": "0.43", "high_dollars": "0.45",
                           "low_dollars": "0.42", "close_dollars": "0.44"},
                 "volume_fp": "90.00", "open_interest_fp": "3800.00"},
            ],
        }),
    })
    df = M.fetch_pm_bars(
        1790000000000, 1790003600000,
        platforms=["kalshi"],
        market_ids={"kalshi": ["KXFUTUREBTC-25DEC31"]},
        interval="1h",
    )
    assert df.columns == list(PM_BAR_COLUMNS)
    assert df.height == 2
    assert df["platform"][0] == "kalshi"
    assert df["close"].to_list() == [0.43, 0.44]
    assert df["open"].to_list() == [0.42, 0.43]
    assert df["high"].to_list() == [0.44, 0.45]
    assert df["low"].to_list() == [0.41, 0.42]
    assert df["volume"].to_list() == [120.0, 90.0]


# --- warn-and-continue -----------------------------------------------------

def test_dead_contract_does_not_kill_snapshot(monkeypatch):
    _route(monkeypatch, {
        "gamma-api.polymarket.com/markets/559651": lambda _u, _p: _j(_GAMMA_MKT),
        "clob.polymarket.com/book": Exception("404 no book"),
    })
    df = M.fetch_pm_snapshot(platforms=["polymarket"],
                             market_ids={"polymarket": ["559651"]})
    # book fetch failed -> depth/bid/ask sizes null, but row still present (price from gamma)
    assert df.height == 1
    r = df.row(0, named=True)
    assert r["last_price"] == pytest.approx(0.425)
    assert r["yes_bid"] == 0.42
    assert r["yes_ask"] == 0.43


def test_history_failure_warns_and_returns_empty(monkeypatch):
    _route(monkeypatch, {
        "clob.polymarket.com/prices-history": Exception("rate limited"),
        "gamma-api.polymarket.com/markets/559651": lambda _u, _p: _j(_GAMMA_MKT),
    })
    df = M.fetch_pm_bars(1790000000000, 1790003600000,
                         platforms=["polymarket"],
                         market_ids={"polymarket": ["559651"]})
    assert df.columns == list(PM_BAR_COLUMNS)
    assert df.height == 0


def test_resolve_token_maps_market_to_yes_token(monkeypatch):
    c = M.PolymarketClient()

    def fake_get(url, params=None, headers=None):
        # Gamma /markets/{id} returns the market (incl. clobTokenIds) by numeric id.
        return json.dumps({"id": "559651",
                           "clobTokenIds": '["32338220190071351435772801779725302244575775216413325951443816017994629993401"]'}).encode()

    monkeypatch.setattr(M, "_get", fake_get)
    # numeric Gamma id -> looked up -> Yes token
    assert c._resolve_token("559651") == "32338220190071351435772801779725302244575775216413325951443816017994629993401"
    # bare numeric token id passes through unchanged
    assert c._resolve_token("32338220190071351435772801779725302244575775216413325951443816017994629993401") == \
        "32338220190071351435772801779725302244575775216413325951443816017994629993401"


# --- sentiment aggregation -------------------------------------------------

def test_sentiment_empty_and_aggregation():
    assert M.build_pm_sentiment(pl.DataFrame(schema=M._PM_DTYPES)) == {}

    snap = pl.DataFrame([{
        "timestamp": 1790000000000, "platform": "polymarket",
        "market_id": "559651", "event_id": "559651",
        "question": "BTC above $110k by Dec 2025?", "yes_bid": 0.42,
        "yes_ask": 0.43, "last_price": 0.425, "spread": 0.01,
        "mid_price": 0.425, "best_bid_size": 100.0, "best_ask_size": 150.0,
        "depth_bids": 300.0, "depth_asks": 400.0, "liquidity": 503466.9,
        "volume_24h": 91374.5, "volume_total": 13814385.37,
        "open_interest": 3955881.3, "close_date": 1767243540000, "status": "open",
    }], schema_overrides=M._PM_DTYPES)
    s = M.build_pm_sentiment(snap)
    assert s["pm_n_contracts"] == 1
    assert s["pm_yes_prob_mean"] == pytest.approx(0.425)
    assert s["pm_depth_imbalance"] == pytest.approx(-100 / 700)
    assert s["pm_volume_24h_total"] == pytest.approx(91374.5)
    assert s["pm_oi_total"] == pytest.approx(3955881.3)


def test_point_in_time_history_no_future_leak(monkeypatch):
    """History timestamps must stay within [start, end); later samples excluded."""
    _route(monkeypatch, {
        "gamma-api.polymarket.com/markets/559651": lambda _u, _p: _j(_GAMMA_MKT),
        "clob.polymarket.com/prices-history": lambda _u, _p: _j({
            "history": [
                {"t": 1790000000, "p": 0.42},
                {"t": 1790000120, "p": 0.43},  # in range
                {"t": 1790000600, "p": 0.55},  # AFTER end -> must not appear
            ]
        }),
    })
    df = M.fetch_pm_bars(1790000000000, 1790000200000,
                        platforms=["polymarket"],
                        market_ids={"polymarket": ["559651"]})
    assert df["close"].to_list() == [0.42, 0.43]
    assert 0.55 not in df["close"].to_list()
