"""Binance USDT-perp historical data: 1m klines + funding + open interest -> BAR_COLUMNS.

Point-in-time merge: for bar t only funding/OI events with timestamp <= t are visible,
joined backward (asof) then held (forward-fill). Failures are per-request/per-day:
log a warning and continue; only an empty kline result aborts the caller.
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone

import polars as pl

import requests

from jev_trading.contracts import BAR_COLUMNS


def _progress(message: str) -> None:
    """Fetch progress goes to stderr so CLI stdout stays machine-readable."""
    print(message, file=sys.stderr, flush=True)


SYMBOL = "BTCUSDT"
KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
OI_URL = (
    "https://data.binance.vision/data/futures/um/daily/metrics/"
    "{symbol}/{symbol}-metrics-{date}.zip"
)
# Bulk monthly klines: 1 file/month vs ~44 paginated API calls. Authoritative,
# free, no credentials (unlike Kaggle mirrors). Complete months only; the
# archive lags a few days, so head/tail edges fall back to fapi.
BULK_KLINES_URL = (
    "https://data.binance.vision/data/futures/um/monthly/klines/"
    "{symbol}/1m/{symbol}-1m-{ym}.zip"
)
BULK_LAG_MS = 3 * 86_400_000  # archive this fresh may not be published yet

KLINES_LIMIT = 1500
FUNDING_LIMIT = 1000
MINUTE_MS = 60_000
DAY_MS = 86_400_000
# Events fetched this far before start so bar 0 already has funding/OI (still ts <= t).
LOOKBACK_MS = 2 * DAY_MS

_OHLCV_SCHEMA = {
    "timestamp": pl.Int64,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
}
_FUNDING_SCHEMA = {"timestamp": pl.Int64, "funding_rate": pl.Float64}
_OI_SCHEMA = {"timestamp": pl.Int64, "open_interest": pl.Float64}


def _get(url: str, params: dict | None = None) -> bytes:
    """GET helper (the single network touchpoint; tests monkeypatch this)."""
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.content
    finally:
        time.sleep(0.2)  # pace pages/requests


def _empty(schema: dict) -> pl.DataFrame:
    return pl.DataFrame(schema=schema)


def fetch_klines(start_ms: int, end_ms: int, symbol: str = SYMBOL) -> pl.DataFrame:
    """1m klines for [start_ms, end_ms); columns timestamp/open/high/low/close/volume."""
    rows: list[list] = []
    cursor = start_ms
    page = 0
    while cursor < end_ms:
        try:
            raw = json.loads(
                _get(
                    KLINES_URL,
                    {
                        "symbol": symbol,
                        "interval": "1m",
                        "startTime": cursor,
                        "endTime": end_ms,
                        "limit": KLINES_LIMIT,
                    },
                )
            )
        except Exception as e:  # noqa: BLE001 — one bad page must not kill the run
            _progress(f"WARN: klines page @{cursor}: {e}")
            break
        if not raw:
            break
        page += 1
        rows.extend(raw)
        _progress(f"klines page {page}: +{len(raw)} rows (t={raw[-1][0]})")
        nxt = int(raw[-1][0]) + MINUTE_MS
        if nxt <= cursor or len(raw) < KLINES_LIMIT:
            break
        cursor = nxt
    if not rows:
        return _empty(_OHLCV_SCHEMA)
    keep = [k for k in rows if start_ms <= int(k[0]) < end_ms]
    return (
        pl.DataFrame(
            {
                "timestamp": [int(k[0]) for k in keep],
                "open": [float(k[1]) for k in keep],
                "high": [float(k[2]) for k in keep],
                "low": [float(k[3]) for k in keep],
                "close": [float(k[4]) for k in keep],
                "volume": [float(k[5]) for k in keep],
            },
            schema=_OHLCV_SCHEMA,
        )
        .unique(subset=["timestamp"], keep="last")
        .sort("timestamp")
    )


def fetch_funding(start_ms: int, end_ms: int, symbol: str = SYMBOL) -> pl.DataFrame:
    """Historical funding rates for (start_ms, end_ms]; columns timestamp/funding_rate."""
    events: list[dict] = []
    cursor = start_ms
    page = 0
    while cursor < end_ms:
        try:
            raw = json.loads(
                _get(
                    FUNDING_URL,
                    {
                        "symbol": symbol,
                        "startTime": cursor,
                        "endTime": end_ms,
                        "limit": FUNDING_LIMIT,
                    },
                )
            )
        except Exception as e:  # noqa: BLE001
            _progress(f"WARN: funding page @{cursor}: {e}")
            break
        if not raw:
            break
        page += 1
        events.extend(raw)
        _progress(f"funding page {page}: +{len(raw)} events")
        nxt = int(raw[-1]["fundingTime"]) + 1
        if nxt <= cursor or len(raw) < FUNDING_LIMIT:
            break
        cursor = nxt
    if not events:
        return _empty(_FUNDING_SCHEMA)
    return (
        pl.DataFrame(
            {
                "timestamp": [int(e["fundingTime"]) for e in events],
                "funding_rate": [float(e["fundingRate"]) for e in events],
            },
            schema=_FUNDING_SCHEMA,
        )
        .unique(subset=["timestamp"], keep="last")
        .sort("timestamp")
    )


def _parse_metrics_zip(blob: bytes) -> pl.DataFrame:
    """One daily metrics zip -> timestamp (epoch ms) / open_interest (base units)."""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = sorted(zf.namelist())[0]
        text = zf.read(name).decode("utf-8")
    df = pl.read_csv(io.StringIO(text))
    if "sum_open_interest" not in df.columns:
        raise ValueError(f"no sum_open_interest in {df.columns}")
    # Real header uses create_time; some mirrors use timestamp/datetime — accept either.
    tcol = next((c for c in ("timestamp", "create_time", "datetime") if c in df.columns), None)
    if tcol is None:
        raise ValueError(f"no time column in {df.columns}")
    ts = df[tcol]
    if ts.dtype in (pl.Int64, pl.UInt64):
        ts_ms = ts.cast(pl.Int64)
    else:
        ts_ms = ts.str.strptime(pl.Datetime("ms"), strict=False).dt.epoch("ms")
    return pl.DataFrame(
        {"timestamp": ts_ms, "open_interest": df["sum_open_interest"].cast(pl.Float64)},
        schema=_OI_SCHEMA,
    ).drop_nulls()


def fetch_open_interest(start_ms: int, end_ms: int, symbol: str = SYMBOL) -> pl.DataFrame:
    """Daily metrics zips covering [start_ms, end_ms); failed days -> warn + skip."""
    day = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date()
    last_day = datetime.fromtimestamp((end_ms - 1) / 1000, tz=timezone.utc).date()
    frames: list[pl.DataFrame] = []
    while day <= last_day:
        url = OI_URL.format(symbol=symbol, date=day.isoformat())
        try:
            frames.append(_parse_metrics_zip(_get(url)))
            _progress(f"OI {day}: ok")
        except Exception as e:  # noqa: BLE001 — missing/not-yet-published day is fine
            _progress(f"WARN: OI {day}: {e}")
        day += timedelta(days=1)
    if not frames:
        return _empty(_OI_SCHEMA)
    return (
        pl.concat(frames)
        .filter((pl.col("timestamp") >= start_ms) & (pl.col("timestamp") < end_ms))
        .unique(subset=["timestamp"], keep="last")
        .sort("timestamp")
    )


def _finalize(df: pl.DataFrame) -> pl.DataFrame:
    """Exactly BAR_COLUMNS, correct dtypes, strictly increasing unique ms timestamps."""
    missing = [c for c in BAR_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    return (
        df.select(list(BAR_COLUMNS))
        .with_columns(
            pl.col("timestamp").cast(pl.Int64),
            *[pl.col(c).cast(pl.Float64) for c in BAR_COLUMNS[1:]],
        )
        .sort("timestamp")
        .unique(subset=["timestamp"], keep="last")
    )


def merge_enrichment(
    bars: pl.DataFrame, funding: pl.DataFrame, oi: pl.DataFrame
) -> pl.DataFrame:
    """Point-in-time join: bar t sees only events with timestamp <= t (backward asof).

    Leading bars before the first event stay null (backfill would leak the future).
    """
    df = bars.sort("timestamp")
    for events, col in ((funding, "funding_rate"), (oi, "open_interest")):
        if col in df.columns:
            continue
        if events.height:
            df = df.join_asof(
                events.sort("timestamp"),
                left_on="timestamp",
                right_on="timestamp",
                strategy="backward",
            )
        else:
            df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias(col))
    return _finalize(df)


def _month_start_ms(year: int, month: int) -> int:
    return int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _months_in(start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    """Calendar months overlapping [start_ms, end_ms)."""
    d = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).replace(day=1)
    out: list[tuple[int, int]] = []
    while int(d.timestamp() * 1000) < end_ms:
        out.append((d.year, d.month))
        d = (d + timedelta(days=32)).replace(day=1)
    return out


def _parse_klines_zip(blob: bytes) -> pl.DataFrame:
    """One monthly klines zip -> timestamp/open/high/low/close/volume."""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next((n for n in zf.namelist() if n.endswith(".csv")), zf.namelist()[0])
        text = zf.read(name).decode("utf-8")
    df = pl.read_csv(io.StringIO(text))
    return pl.DataFrame(
        {
            "timestamp": df["open_time"].cast(pl.Int64),
            "open": df["open"].cast(pl.Float64),
            "high": df["high"].cast(pl.Float64),
            "low": df["low"].cast(pl.Float64),
            "close": df["close"].cast(pl.Float64),
            "volume": df["volume"].cast(pl.Float64),
        },
        schema=_OHLCV_SCHEMA,
    ).sort("timestamp")


def fetch_klines_month(year: int, month: int, symbol: str = SYMBOL) -> pl.DataFrame:
    """Single complete calendar month from the bulk archive. Raises on failure."""
    ym = f"{year:04d}-{month:02d}"
    url = BULK_KLINES_URL.format(symbol=symbol, ym=ym)
    return _parse_klines_zip(_get(url))


def fetch_bars(start_ms: int, end_ms: int, symbol: str = SYMBOL) -> pl.DataFrame:
    """Full pipeline for [start_ms, end_ms) -> BAR_COLUMNS frame. Raises if no klines.

    Klines strategy: bulk monthly zips for complete calendar months fully inside
    the range (up to ~44x fewer requests than fapi pagination); fapi paginated
    fetch for head/tail edges, unpublished months, and any failed bulk month.
    """
    if start_ms >= end_ms:
        raise ValueError(f"empty range: [{start_ms}, {end_ms})")
    now_ms = int(time.time() * 1000)
    frames: list[pl.DataFrame] = []
    covered: list[tuple[int, int]] = []  # bulk-covered intervals within [start, end)
    for year, month in _months_in(start_ms, end_ms):
        mstart, mend = _month_start_ms(year, month), _month_start_ms(
            year + (month == 12), month % 12 + 1
        )
        if mstart >= start_ms and mend <= end_ms and mend <= now_ms - BULK_LAG_MS:
            try:
                bulk = fetch_klines_month(year, month, symbol).filter(
                    (pl.col("timestamp") >= start_ms) & (pl.col("timestamp") < end_ms)
                )
                if not bulk.is_empty():
                    frames.append(bulk)
                    covered.append((mstart, mend))
                    _progress(f"klines {year:04d}-{month:02d}: bulk ok ({bulk.height} rows)")
                    continue
            except Exception as e:  # noqa: BLE001 — fall through to fapi
                _progress(f"WARN: klines {year:04d}-{month:02d} bulk failed ({e}), using fapi")
    # fapi-fill everything not bulk-covered: head, tail, failed months.
    gaps: list[tuple[int, int]] = []
    cursor = start_ms
    for cstart, cend in sorted(covered):
        if cursor < cstart:
            gaps.append((cursor, cstart))
        cursor = max(cursor, cend)
    if cursor < end_ms:
        gaps.append((cursor, end_ms))
    for gstart, gend in gaps:
        gap = fetch_klines(gstart, gend, symbol)
        if not gap.is_empty():
            frames.append(gap)
    bars = (
        pl.concat(frames).unique(subset=["timestamp"], keep="last").sort("timestamp")
        if frames
        else _empty(_OHLCV_SCHEMA)
    )
    if bars.is_empty():
        raise ValueError(f"no klines for [{start_ms}, {end_ms})")
    funding = fetch_funding(start_ms - LOOKBACK_MS, end_ms, symbol)
    oi = fetch_open_interest(start_ms - LOOKBACK_MS, end_ms, symbol)
    return merge_enrichment(bars, funding, oi)


def merge_gap(
    existing: pl.DataFrame | None, new: pl.DataFrame, start_ms: int, end_ms: int
) -> pl.DataFrame:
    """Idempotent gap replace: keep existing rows outside [start_ms, end_ms), take `new` inside."""
    if existing is None or existing.is_empty():
        merged = new
    else:
        keep = existing.filter(
            (pl.col("timestamp") < start_ms) | (pl.col("timestamp") >= end_ms)
        )
        merged = pl.concat([keep, new], how="diagonal_relaxed")
    return _finalize(merged)
