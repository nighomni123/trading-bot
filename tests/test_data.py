"""Unit tests for the data pipeline — network fully disabled (only `_get` is mocked)."""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone

import polars as pl
import pytest

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.data import fetch as F

MIN = 60_000


def _ms(*args: int) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp() * 1000)


def _kline(ts: int, close: str = "100.5") -> list:
    return [ts, "100.0", "101.0", "99.0", close, "12.5", ts + 59_999, "0", 42, "0", "0", "0"]


def _j(payload) -> bytes:
    return json.dumps(payload).encode()


def _metrics_zip(day: str, rows: list[tuple[str, float]]) -> bytes:
    lines = ["create_time,symbol,sum_open_interest,sum_open_interest_value"]
    lines += [f"{ts},BTCUSDT,{oi},{oi * 50_000}" for ts, oi in rows]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"BTCUSDT-metrics-{day}.csv", "\n".join(lines))
    return buf.getvalue()


def _route(monkeypatch, handlers: dict) -> None:
    """handlers: url-substring -> bytes | Exception | callable(url, params) -> bytes|raises."""

    def fake_get(url: str, params: dict | None = None) -> bytes:
        for key, fn in handlers.items():
            if key in url:
                out = fn(url, params) if callable(fn) else fn
                if isinstance(out, Exception):
                    raise out
                return out
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(F, "_get", fake_get)


def _bar_frame(start: int, n: int, close: float) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": [start + i * MIN for i in range(n)],
            "open": [close] * n,
            "high": [close] * n,
            "low": [close] * n,
            "close": [close] * n,
            "volume": [1.0] * n,
            "funding_rate": [0.1] * n,
            "open_interest": [1000.0] * n,
        },
        schema={c: pl.Int64 if c == "timestamp" else pl.Float64 for c in BAR_COLUMNS},
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("network disabled in tests")

    monkeypatch.setattr(F.requests, "get", boom)


def _basic_handlers(start: int, end: int, n_bars: int, funding: list, oi: dict):
    klines = [_kline(start + i * MIN) for i in range(n_bars)]

    def oi_get(url, _params):
        day = re.search(r"metrics-(\d{4}-\d{2}-\d{2})", url).group(1)
        val = oi.get(day, Exception(f"404 {day}"))
        return val

    return {
        "/fapi/v1/klines": lambda _u, _p: _j(klines),
        "/fapi/v1/fundingRate": lambda _u, _p: _j(funding),
        "metrics": oi_get,
    }


def test_schema_dtypes_and_monotonic(monkeypatch):
    start, n = _ms(2026, 9, 10), 10
    end = start + n * MIN
    funding = [{"fundingTime": start - 8 * 3_600_000, "fundingRate": "0.0001"}]
    oi = {
        "2026-09-08": _metrics_zip("2026-09-08", [("2026-09-08 00:00:00", 100.0)]),
        "2026-09-09": Exception("OI not published"),
        "2026-09-10": _metrics_zip("2026-09-10", [("2026-09-10 00:05:00", 110.0)]),
    }
    _route(monkeypatch, _basic_handlers(start, end, n, funding, oi))

    df = F.fetch_bars(start, end)

    assert df.columns == list(BAR_COLUMNS)
    assert df.schema["timestamp"] == pl.Int64
    assert all(df.schema[c] == pl.Float64 for c in BAR_COLUMNS[1:])
    assert df.height == n
    ts = df["timestamp"].to_list()
    assert all(b > a for a, b in zip(ts, ts[1:])), "timestamps must be strictly increasing"
    assert len(set(ts)) == len(ts), "no duplicate timestamps"
    assert all(b - a == MIN for a, b in zip(ts, ts[1:])), "contiguous 1m grid"
    assert df["funding_rate"][0] == pytest.approx(1e-4)
    assert df["open_interest"][0] == pytest.approx(100.0), "lookback OI visible at bar 0"
    assert df["open_interest"][5] == pytest.approx(110.0), "same-day OI visible from its ts"
    assert df["open_interest"][4] == pytest.approx(100.0), "bar before OI ts keeps prior value"


def test_point_in_time_funding_never_leaks_future(monkeypatch):
    start, n = _ms(2026, 9, 10), 10
    end = start + n * MIN
    funding = [
        {"fundingTime": start - 8 * 3_600_000, "fundingRate": "0.0001"},  # before range
        {"fundingTime": start + 5 * MIN, "fundingRate": "0.0002"},  # mid-range
        {"fundingTime": end + 60 * MIN, "fundingRate": "0.0003"},  # after every bar
    ]
    _route(monkeypatch, _basic_handlers(start, end, n, funding, {}))

    df = F.fetch_bars(start, end)
    rates = df["funding_rate"].to_list()

    assert all(r == pytest.approx(1e-4) for r in rates[:5]), "bar t must not see funding > t"
    assert all(r == pytest.approx(2e-4) for r in rates[5:]), "event visible at ts == t"
    assert all(r != pytest.approx(3e-4) for r in rates), "post-range funding must never appear"
    assert df["open_interest"].null_count() == n, "failed OI days yield nulls, not a crash"


def test_klines_pagination(monkeypatch):
    monkeypatch.setattr(F, "KLINES_LIMIT", 3)
    start, n = _ms(2026, 9, 10), 10
    end = start + n * MIN
    all_klines = [_kline(start + i * MIN) for i in range(n)]
    calls: list[dict] = []

    def klines_get(_url, params):
        calls.append(dict(params))
        page = [k for k in all_klines if params["startTime"] <= k[0] < params["endTime"]]
        return _j(page[: params["limit"]])

    _route(
        monkeypatch,
        {
            "/fapi/v1/klines": klines_get,
            "/fapi/v1/fundingRate": lambda _u, _p: _j([]),
            "metrics": Exception("404"),
        },
    )

    df = F.fetch_bars(start, end)
    assert df.height == n
    assert len(calls) == 4, "10 bars / limit 3 -> pages of 3,3,3,1"
    assert all(c["limit"] == 3 for c in calls)


def test_enrichment_failures_do_not_crash(monkeypatch):
    start, n = _ms(2026, 9, 10), 5
    end = start + n * MIN
    _route(
        monkeypatch,
        {
            "/fapi/v1/klines": lambda _u, _p: _j([_kline(start + i * MIN) for i in range(n)]),
            "/fapi/v1/fundingRate": Exception("rate limited"),
            "metrics": Exception("404"),
        },
    )

    df = F.fetch_bars(start, end)
    assert df.columns == list(BAR_COLUMNS)
    assert df.height == n
    assert df["funding_rate"].null_count() == n
    assert df["open_interest"].null_count() == n


def _bulk_zip(year_month: str, rows: list[tuple[int, str]]) -> bytes:
    lines = [
        "open_time,open,high,low,close,volume,close_time,"
        "quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"
    ]
    lines += [
        f"{ts},100.0,101.0,99.0,{c},12.5,{ts + 59_999},0,42,0,0,0" for ts, c in rows
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"BTCUSDT-1m-{year_month}.csv", "\n".join(lines))
    return buf.getvalue()


def test_bulk_month_used_for_complete_past_month(monkeypatch):
    start, end = _ms(2024, 3, 1), _ms(2024, 4, 1)  # past month -> archive complete
    n = (end - start) // MIN
    rows = [(start + i * MIN, "100.5") for i in range(n)]
    fapi_calls: list[int] = []
    _route(
        monkeypatch,
        {
            "monthly/klines": lambda _u, _p: _bulk_zip("2024-03", rows),
            "/fapi/v1/klines": lambda _u, _p: fapi_calls.append(1) or _j([]),
            "/fapi/v1/fundingRate": lambda _u, _p: _j([]),
            "metrics": Exception("404"),
        },
    )

    df = F.fetch_bars(start, end)
    assert df.height == n, "full month must come from one bulk zip"
    assert not fapi_calls, "no paginated fapi calls when bulk covers the range"
    assert df["close"][0] == pytest.approx(100.5)


def test_bulk_failure_falls_back_to_fapi(monkeypatch):
    start, end = _ms(2024, 3, 1), _ms(2024, 4, 1)
    klines = [_kline(start + i * MIN) for i in range(5)]
    _route(
        monkeypatch,
        {
            "monthly/klines": Exception("404"),
            "/fapi/v1/klines": lambda _u, _p: _j(klines),
            "/fapi/v1/fundingRate": lambda _u, _p: _j([]),
            "metrics": Exception("404"),
        },
    )

    df = F.fetch_bars(start, end)
    assert df.height == 5, "failed bulk month must be covered by fapi"


def test_gap_merge_keeps_outside_and_is_idempotent():
    start, total = _ms(2026, 9, 10), 20
    gap_s, gap_e = start + 5 * MIN, start + 15 * MIN
    existing = _bar_frame(start, total, close=1.0)
    new = pl.DataFrame(
        {
            "timestamp": list(range(gap_s, gap_e, MIN)),
            "open": [2.0] * 10,
            "high": [2.0] * 10,
            "low": [2.0] * 10,
            "close": [2.0] * 10,
            "volume": [2.0] * 10,
            "funding_rate": [0.2] * 10,
            "open_interest": [2000.0] * 10,
        },
        schema={c: pl.Int64 if c == "timestamp" else pl.Float64 for c in BAR_COLUMNS},
    )

    m1 = F.merge_gap(existing, new, gap_s, gap_e)
    assert m1.columns == list(BAR_COLUMNS)
    assert m1.height == total
    ts = m1["timestamp"].to_list()
    assert all(b > a for a, b in zip(ts, ts[1:])), "output sorted + unique"
    outside = m1.filter((pl.col("timestamp") < gap_s) | (pl.col("timestamp") >= gap_e))
    inside = m1.filter((pl.col("timestamp") >= gap_s) & (pl.col("timestamp") < gap_e))
    assert outside["close"].to_list() == [1.0] * 10, "rows outside the gap preserved"
    assert inside["close"].to_list() == [2.0] * 10, "gap replaced by new fetch"

    m2 = F.merge_gap(m1, new, gap_s, gap_e)
    assert m1.equals(m2), "re-running the same fetch must be a no-op"

    from_empty = F.merge_gap(None, new, gap_s, gap_e)
    assert from_empty.equals(F._finalize(new))
