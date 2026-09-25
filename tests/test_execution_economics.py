"""Stage 10 execution-economics tests. No network; deterministic doubles."""
from __future__ import annotations

import pytest

from jev_trading.quant.execution_economics import (
    CostMeasurement, CostProfile, build_profiles, measure_book, profile_total_bps,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, book, depth):
        self.book, self.depth = book, depth
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return FakeResponse(self.depth if "depth" in url else self.book)


def test_measure_book_records_spread_and_slippage():
    book = {"bidPrice": "100.0", "askPrice": "100.02"}
    depth = {"bids": [["100.0", "10.0"], ["99.9", "50.0"]], "asks": [["100.02", "10.0"], ["100.1", "50.0"]]}
    m = measure_book(symbol="BTCUSDT", samples=3, interval=0, notional_usd=1000, session=FakeSession(book, depth))
    s = m.summary()
    assert s["venue_reachable"] and s["samples"] == 3
    # spread = 0.02 / 100.01 ≈ 2 bps in this synthetic book
    assert s["spread_bps"]["median"] == pytest.approx(2.0, rel=0.01)
    # crossing ~1000 notional eats past the top level, so taker slippage > spread/2
    assert s["taker_slippage_bps"]["median"] > s["spread_bps"]["median"] / 2


def test_slippage_is_bounded_and_plausible():
    """A book walk can never produce slippage near 100% of price."""
    book = {"bidPrice": "100.0", "askPrice": "100.02"}
    depth = {"bids": [["100.0", "10.0"], ["99.9", "50.0"]], "asks": [["100.02", "10.0"], ["100.1", "50.0"]]}
    m = measure_book(symbol="BTCUSDT", samples=3, interval=0, notional_usd=1000, session=FakeSession(book, depth))
    s = m.summary()
    for key in ("taker_slippage_bps", "maker_slippage_bps"):
        med = s[key]["median"]
        assert med is not None
        assert 0 <= med < 100, f"{key}={med} is not a plausible slippage"
        assert med < 50, f"{key}={med} exceeds a sane bound"


def test_measure_book_marks_unreachable_venue():
    class Boom:
        def get(self, *a, **k):
            raise ConnectionError("offline")
    m = measure_book(samples=1, session=Boom())
    s = m.summary()
    assert s["venue_reachable"] is False and s["error"] == "ConnectionError"


def test_profiles_separate_measured_from_assumed():
    measured = CostMeasurement(
        spread_bps=[0.012], taker_slippage_bps=[0.05], maker_slippage_bps=[0.01],
        samples=1, venue_reachable=True,
    )
    profiles = build_profiles(measured)
    by_name = {p.name: p for p in profiles}
    assert by_name["configured_taker_taker"].slippage_source == "assumed"
    assert by_name["measured_taker_taker"].slippage_source == "measured"
    assert by_name["measured_maker_maker"].achievable is False


def test_maker_maker_profile_is_never_deemed_achievable():
    measured = CostMeasurement(taker_slippage_bps=[0.05], maker_slippage_bps=[0.01], samples=1)
    mm = [p for p in build_profiles(measured) if p.execution_mode == "maker/maker"]
    assert mm and all(not p.achievable for p in mm)


def test_unmeasured_slippage_falls_back_to_assumed_with_provenance():
    profiles = build_profiles(None)
    assert all(p.slippage_source in {"measured", "assumed"} for p in profiles)
    assert build_profiles(None)[0].slippage_source == "assumed"
