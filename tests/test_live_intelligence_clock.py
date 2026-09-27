"""Venue clock synchronization: calibration, correction, and fail-closed bounds.

The governing rule is that a clock mechanism must never be able to move an
event into the past and thereby leak future information. Every test here
either proves the correction is bounded, or proves that an implausible or
unstable calibration disables correction entirely.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jev_trading.data.clock import ClockSample, VenueClock, midpoint_offset
from jev_trading.data.venue_adapters import (
    BinanceLivePerpAdapter,
    BinanceLiveState,
    BybitPerpAdapter,
    BybitLiveState,
    _bybit_server_ms,
    build_venue_clock,
)
from jev_trading.live_intelligence.config import ClockConfig

T0_MS = 1_700_000_000_000


def at(offset_ms: int = 0) -> datetime:
    return datetime.fromtimestamp((T0_MS + offset_ms) / 1000, tz=timezone.utc)


def clock_with(offsets: list[int], *, rtt_ms: int = 200, **kwargs) -> VenueClock:
    """A clock pre-loaded with a known offset pattern (no network)."""
    clock = VenueClock("binance", "u", lambda payload: 0, **kwargs)
    for index, offset in enumerate(offsets):
        clock._ingest(ClockSample(offset_ms=offset, rtt_ms=rtt_ms, observed_at=index))
    return clock


# --------------------------------------------------------------- estimator


def test_midpoint_offset_is_server_minus_bracket_centre():
    # Local sent at .000, venue answered .187, local received at .024.
    offset, rtt = midpoint_offset(T0_MS + 187, T0_MS, T0_MS + 24)
    assert offset == 175
    assert rtt == 24


def test_median_offset_ignores_a_cold_outlier():
    # A cold first request measured +697 ms while the warm median was +176 ms.
    # It must not survive, and `calibrate()` keeps sampling until 5 agree.
    clock = clock_with([180, 182, 177, 180, 697, 179])
    assert clock.healthy is True
    assert clock.offset_ms == 180
    assert clock.offset_ms != 697


def test_extreme_outlier_is_trimmed_from_the_window():
    # One 50 s reading among agreeing samples cannot enter the median.
    clock = clock_with([180, 181, 179, 182, 180, 50_000], max_offset_ms=2_000)
    assert clock.healthy is True
    assert clock.offset_ms == 180


def test_too_few_agreeing_samples_fails_closed():
    # 4 agreeing against 1 wild is not enough evidence to correct anything.
    clock = clock_with([180, 181, 179, 182, 50_000], max_offset_ms=2_000)
    assert clock.healthy is False
    assert clock.status == "UNHEALTHY"
    assert clock.correct(T0_MS) == T0_MS


def test_unstable_clock_does_not_jump_to_the_outlier():
    clock = clock_with([180, 182, 177, 180, 697, 179])
    # The outlier must not become the active calibration.
    assert abs(clock.offset_ms - 180) <= 5
    assert clock.correct(T0_MS) == T0_MS - clock.offset_ms


def test_excessive_skew_fails_closed_and_does_not_correct():
    clock = clock_with([2_500] * 5, max_offset_ms=2_000)
    assert clock.healthy is False
    assert clock.status == "UNHEALTHY"
    # No correction at all: the caller keeps rejecting the feed.
    assert clock.correct(T0_MS) == T0_MS


def test_high_uncertainty_fails_closed():
    # A slow link cannot bound the correction tightly enough to be trusted.
    clock = clock_with([180] * 5, rtt_ms=2_000, max_uncertainty_ms=600)
    assert clock.healthy is False
    assert clock.correct(T0_MS) == T0_MS


def test_uncalibrated_clock_is_a_no_op():
    clock = VenueClock("binance", "u", lambda payload: 0)
    assert clock.healthy is False
    assert clock.correct(T0_MS) == T0_MS


def test_insufficient_samples_never_claim_health():
    clock = clock_with([180, 181, 182, 183], bootstrap_samples=5)
    assert clock.status == "UNSYNCED"
    assert clock.correct(T0_MS) == T0_MS


def test_uncertainty_grows_with_observed_spread():
    tight = clock_with([180] * 5, rtt_ms=200)
    loose = clock_with([100, 140, 180, 220, 260], rtt_ms=200)
    assert tight.offset_ms == loose.offset_ms == 180
    assert loose.uncertainty_ms > tight.uncertainty_ms


def test_failed_sample_never_raises_and_never_corrupts():
    def boom(_url):
        raise TimeoutError("venue unreachable")

    clock = VenueClock("binance", "u", lambda p: 0, fetch=boom)
    assert clock.sample_once() is None
    assert clock.correct(T0_MS) == T0_MS
    assert "TimeoutError" in clock.health()["last_error"]


def test_health_reports_offset_uncertainty_and_samples():
    clock = clock_with([180] * 5)
    health = clock.health()
    assert health["status"] == "HEALTHY"
    assert health["offset_ms"] == 180
    assert health["samples"] == 5
    assert health["max_observed_offset_ms"] == 180


# ------------------------------------------------------------------ Binance


def test_clock_skewed_valid_event_is_accepted_after_correction():
    state = BinanceLiveState(clock=clock_with([180] * 5))
    state.apply({"e": "ticker", "E": T0_MS + 180, "s": "BTCUSDT", "c": "100"}, 1)
    fields = state.fields(at())
    assert fields is not None
    # The 180 ms lead is cancelled exactly: the event is now comparable to
    # local time rather than 180 ms in the future.
    assert fields["event_timestamp"] == at()
    assert fields["metadata"]["raw_event_timestamp"] == T0_MS + 180


def test_genuine_future_event_is_still_rejected_after_correction():
    """Test F: the correction must not open a look-ahead hole.

    A healthy 180 ms calibration cannot excuse an event that is 500 ms ahead of
    the *corrected* timeline — only of the uncorrected one.
    """
    state = BinanceLiveState(clock=clock_with([180] * 5))
    state.apply({"e": "ticker", "E": T0_MS + 180 + 500, "s": "BTCUSDT", "c": "100"}, 1)
    assert state.fields(at()) is None


def test_provenance_keeps_raw_corrected_offset_and_uncertainty():
    clock = clock_with([180] * 5, rtt_ms=200)
    state = BinanceLiveState(clock=clock)
    state.apply({"e": "ticker", "E": T0_MS + 180, "s": "BTCUSDT", "c": "100"}, 1)
    meta = state.fields(at())["metadata"]
    assert meta["raw_event_timestamp"] == T0_MS + 180
    assert meta["last_event_timestamp"] == T0_MS
    assert meta["clock_offset_ms"] == 180
    assert meta["clock_offset_uncertainty_ms"] is not None
    assert meta["clock_status"] == "HEALTHY"


def test_watermark_is_the_oldest_contributing_group():
    state = BinanceLiveState(clock=clock_with([0] * 5))
    state.apply({"e": "ticker", "E": T0_MS - 100, "s": "BTCUSDT", "c": "100"}, 1)
    state.apply({"e": "markPriceUpdate", "E": T0_MS - 400, "s": "BTCUSDT", "p": "100", "i": "100"}, 1)
    state.apply({"e": "depthUpdate", "E": T0_MS - 200, "s": "BTCUSDT",
                 "b": [["99.9", "1"]], "a": [["100.1", "1"]]}, 1)
    fields = state.fields(at())
    meta = fields["metadata"]
    # Oldest of the three groups, not the newest: a composite snapshot is only
    # as current as its slowest component.
    assert meta["observation_watermark_timestamp"] == T0_MS - 400
    assert fields["event_timestamp"] == at(-400)


def test_watermark_excludes_the_rare_liquidation_stream():
    state = BinanceLiveState(clock=clock_with([0] * 5))
    state.apply({"e": "ticker", "E": T0_MS - 100, "s": "BTCUSDT", "c": "100"}, 1)
    # A liquidation 9 s old must not drag the snapshot toward staleness.
    state.apply({"e": "forceOrder", "E": T0_MS - 9_000, "s": "BTCUSDT",
                 "o": {"S": "SELL", "q": "1", "T": T0_MS - 9_000}}, 1)
    fields = state.fields(at())
    assert fields is not None
    assert fields["metadata"]["observation_watermark_timestamp"] == T0_MS - 100


def test_stalled_group_staleness_is_not_hidden_by_a_newer_one():
    state = BinanceLiveState(clock=clock_with([0] * 5))
    state.apply({"e": "ticker", "E": T0_MS - 1, "s": "BTCUSDT", "c": "100"}, 1)
    state.apply({"e": "markPriceUpdate", "E": T0_MS - 60_000, "s": "BTCUSDT", "p": "100", "i": "100"}, 1)
    state.apply({"e": "depthUpdate", "E": T0_MS - 1, "s": "BTCUSDT",
                 "b": [["99.9", "1"]], "a": [["100.1", "1"]]}, 1)
    # The price group is current and the feed is alive, but the mark group is
    # a minute cold: the mark is nulled rather than published as fresh.
    fields = state.fields(at())
    assert fields["last"] == 100.0
    assert fields["mark"] is None


def test_without_a_clock_the_guard_is_exactly_as_strict_as_before():
    state = BinanceLiveState()
    state.apply({"e": "ticker", "E": T0_MS + 1, "s": "BTCUSDT", "c": "100"}, 1)
    assert state.fields(at()) is None


def test_adapter_snapshot_survives_a_corrected_leading_edge():
    clock = clock_with([180] * 5)
    adapter = BinanceLivePerpAdapter(clock=clock)
    adapter._state = BinanceLiveState(clock=clock)
    adapter._state.apply({"e": "ticker", "E": T0_MS + 180, "s": "BTCUSDT", "c": "100"}, 1)
    ticks = adapter.snapshot(now=at())
    assert len(ticks) == 1
    assert ticks[0].metadata["transport"] == "websocket"
    assert adapter.health(now=at()).safe_for_trading is True
    adapter.close()


# -------------------------------------------------------------------- Bybit


def test_bybit_uses_its_own_larger_offset():
    # Measured ~+296 ms for Bybit vs ~+176 ms for Binance; they are not shared.
    clock = clock_with([296] * 5)
    state = BybitLiveState(clock=clock)
    state.apply({"topic": "tickers.BTCUSDT", "ts": T0_MS + 296,
                 "data": {"lastPrice": "100"}}, 1)
    assert state.fields(at()) is not None
    assert state.fields(at())["event_timestamp"] == at()


def test_bybit_server_time_parses_nanoseconds():
    payload = {"result": {"timeSecond": "1700000000", "timeNano": "1700000000123456789"}}
    assert _bybit_server_ms(payload) == 1_700_000_000_123


def test_bybit_leading_edge_is_rejected_without_a_clock():
    state = BybitLiveState()
    state.apply({"topic": "tickers.BTCUSDT", "ts": T0_MS + 1, "data": {"lastPrice": "100"}}, 1)
    assert state.fields(at()) is None


def test_bybit_adapter_wiring_and_lifecycle():
    clock = clock_with([296] * 5)
    adapter = BybitPerpAdapter(clock=clock)
    assert adapter._state.clock is clock
    assert adapter.clock_health()["offset_ms"] == 296
    adapter.close()


# ------------------------------------------------------------------ factory


def test_build_venue_clock_respects_disabled_and_bounds():
    assert build_venue_clock("binance", ClockConfig(enabled=False)) is None
    assert build_venue_clock("binance", None) is None
    clock = build_venue_clock("binance", ClockConfig(max_offset_ms=1_500))
    assert clock.max_offset_ms == 1_500
    bybit = build_venue_clock("bybit", ClockConfig())
    assert bybit.venue == "bybit"


def test_default_config_loads_without_touching_existing_files():
    clock = ClockConfig()
    assert clock.enabled is True
    assert clock.max_offset_ms == 2_000
    assert clock.max_uncertainty_ms == 600
    # Bounds must be strict enough that a bad link fails, not drifts.
    assert clock.max_offset_ms < timedelta(days=1).total_seconds() * 1000


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
