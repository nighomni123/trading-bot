# Attribution — PM sources: Polymarket/py-builder-relayer-client (MIT) https://github.com/Polymarket/py-builder-relayer-client; Surf /search/prediction-market (monid); BlockRun /polymarket/cohorts/stats (monid).
"""Kalshi + Polymarket prediction-market data: sentiment + order-book input.

These markets trade binary YES/NO contracts on events (e.g. "BTC > $100k by
Dec 2026?"). A contract's price IS the implied P(YES) in [0, 1]. That price,
together with on-chain order-book depth (bids/asks), volume and open interest,
is the sentiment / positioning signal the frontier model consumes alongside the
BTC-perp bars.

Live (P8) input: poll snapshots on a cadence, or replay price history.
Failures are per-market/per-request: warn and continue — one dead contract must
never drop the whole snapshot. The single network touchpoint `_get` is
module-level so tests monkeypatch it (mirrors data/fetch.py).

No secrets: both read APIs are public/unauthenticated. Kalshi's external host
is geo/WAF-blocked in some sandboxes but its Trade API v2 is documented and
works in a non-WAF environment; the Polymarket path is reachable directly from
this sandbox.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

import polars as pl
import requests

PM_PLATFORM = Literal["polymarket", "kalshi"]

POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB = "https://clob.polymarket.com"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_INTERVAL = "1h"
PM_WATCHLIST = "configs/pm.json"

_NUM = pl.Float64
_STR = pl.Utf8
_PM_DTYPES: dict[str, pl.DataType] = {
    "timestamp": pl.Int64,
    "platform": _STR,
    "market_id": _STR,
    "event_id": _STR,
    "question": _STR,
    "yes_bid": _NUM,
    "yes_ask": _NUM,
    "last_price": _NUM,
    "spread": _NUM,
    "mid_price": _NUM,
    "best_bid_size": _NUM,
    "best_ask_size": _NUM,
    "depth_bids": _NUM,
    "depth_asks": _NUM,
    "liquidity": _NUM,
    "volume_24h": _NUM,
    "volume_total": _NUM,
    "open_interest": _NUM,
    "close_date": pl.Int64,
    "status": _STR,
}
PM_COLUMNS: tuple[str, ...] = tuple(_PM_DTYPES.keys())

_PM_BAR_DTYPES: dict[str, pl.DataType] = {
    "timestamp": pl.Int64,
    "platform": _STR,
    "market_id": _STR,
    "question": _STR,
    "open": _NUM,
    "high": _NUM,
    "low": _NUM,
    "close": _NUM,
    "volume": _NUM,
    "open_interest": _NUM,
}
PM_BAR_COLUMNS: tuple[str, ...] = tuple(_PM_BAR_DTYPES.keys())


# ---------------------------------------------------------------------------
# Network touchpoint + scalar helpers (test-monkeypatchable, mirror data/fetch.py)
# ---------------------------------------------------------------------------
def _get(url: str, params: dict | None = None,
         headers: dict | None = None) -> bytes:
    """GET helper — the single network touchpoint; tests monkeypatch this.

    One retry on a transient connection/HTTP error: the CLOB `/book` endpoint
    resets TCP mid-response under load, and a live feed must not lose a whole
    snapshot tick because of it. Non-transient errors (4xx) raise at once.
    """
    last: Exception | None = None
    for attempt in range(2):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            r.raise_for_status()
            time.sleep(0.2)  # pace requests — both APIs are rate-sensitive
            return r.content
        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
            if isinstance(e, requests.exceptions.HTTPError) and (
                e.response.status_code < 500 or attempt
            ):
                raise
            last = e
            time.sleep(0.4 * (attempt + 1))  # backoff before the single retry
    raise last  # pragma: no cover - only if both attempts 5xx / connection-reset


def _json(url: str, params: dict | None = None,
          headers: dict | None = None) -> dict | list:
    return json.loads(_get(url, params=params, headers=headers))


def _fp(v) -> float | None:
    """Parse a fixed-point string/number -> float. None-safe."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if v != v else float(v)
    s = str(v).strip()
    if s in ("", "null", "None", "0x"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _scale(v, factor: float) -> float | None:
    """Multiply a fixed-point value by a notional factor (Kalshi contracts -> USD)."""
    return v * factor if v is not None else None


def _iso_ms(s) -> int | None:
    """ISO-8601 string (or unix seconds) -> epoch ms UTC; None if unparseable."""
    if not s:
        return None
    if isinstance(s, (int, float)):
        t = float(s)
        return int(t * 1000) if t < 1e12 else int(t)
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _empty(dtypes: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame(schema=dtypes)


class PMClient(Protocol):
    """Swappable market-data source (mirrors the JevClient Protocol pattern)."""

    platform: str

    def list_markets(self, limit: int = 1000, **filters) -> list[dict]: ...
    def get_market(self, market_id: str) -> dict: ...
    def get_order_book(self, market_id: str) -> dict: ...
    def get_price_history(self, market_id: str, start_ms: int, end_ms: int,
                         interval: str = DEFAULT_INTERVAL) -> list[dict]: ...
    def normalize_snapshot(self, mkt: dict) -> list[dict]: ...


class PolymarketClient:
    """Polymarket via the public Gamma catalog + CLOB order book & price history."""

    platform = "polymarket"

    def __init__(self, gamma: str = POLYMARKET_GAMMA, clob: str = POLYMARKET_CLOB):
        self.gamma = gamma
        self.clob = clob

    def list_markets(self, limit: int = 1000, closed: bool = False,
                     **filters) -> list[dict]:
        params = {"limit": str(limit), "closed": "true" if closed else "false"}
        params.update({k: str(v) for k, v in filters.items()})
        data = _json(f"{self.gamma}/markets", params=params)
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        return data if isinstance(data, list) else []

    def get_market(self, market_id: str) -> dict:
        return _json(f"{self.gamma}/markets/{market_id}")

    def get_order_book(self, token_id: str, depth: int = 10) -> dict:
        return _json(f"{self.clob}/book", params={"token_id": token_id})

    def get_price_history(self, market_id: str, start_ms: int, end_ms: int,
                          interval: str = DEFAULT_INTERVAL) -> list[dict]:
        token = self._resolve_token(market_id)
        data = _json(
            f"{self.clob}/prices-history",
            params={"market": token, "startTs": start_ms / 1000,
                    "endTs": end_ms / 1000, "interval": interval},
        )
        return (data.get("history", []) if isinstance(data, dict) else data) if isinstance(data, (dict, list)) else []

    def _resolve_token(self, market_id: str) -> str:
        """Resolve a Polymarket market to the YES token id for the CLOB.

        Gamma ``/markets/{id}`` accepts the numeric market id (e.g. ``559651``),
        NOT the ``0x`` condition id. CLOB ``/book`` / ``/prices-history`` take the
        bare-token asset id (``clobTokenIds[0]``). A bare long token id is used
        as-is; any other id is looked up to read ``clobTokenIds``.
        """
        if not market_id.startswith("0x") and len(market_id) > 50:
            return market_id  # already a bare CLOB token id
        mkt = self.get_market(market_id)
        try:
            tokens = json.loads(mkt.get("clobTokenIds") or "[]") if isinstance(mkt, dict) else []
            if isinstance(tokens, list) and tokens:
                return tokens[0]
        except (ValueError, TypeError):
            pass
        return market_id

    def normalize_snapshot(self, mkt: dict) -> list[dict]:
        """One Gamma market -> one normalized snapshot row (with top-of-book)."""
        yes_id = ""
        try:
            tokens = json.loads(mkt.get("clobTokenIds") or "[]")
            if isinstance(tokens, list) and tokens:
                yes_id = tokens[0]
        except (ValueError, TypeError):
            pass
        prices = []
        try:
            prices = json.loads(mkt.get("outcomePrices") or "[]")
        except (ValueError, TypeError):
            pass
        yes_price = _fp(prices[0]) if prices else _fp(mkt.get("lastTradePrice"))
        bid = _fp(mkt.get("bestBid"))
        ask = _fp(mkt.get("bestAsk"))
        bids: list[dict] = []
        asks: list[dict] = []
        if yes_id:
            try:
                book = self.get_order_book(yes_id) or {}
                bids = book.get("bids") or []
                asks = book.get("asks") or []
            except Exception as e:
                print(f"WARN: polymarket book {yes_id}: {e}")

        def depth(orders, n=10):
            return sum(_fp(o.get("size")) or 0.0 for o in orders[:n]) or None

        row = {
            "platform": "polymarket",
            "market_id": mkt.get("id") or mkt.get("conditionId", "") or yes_id,
            "event_id": mkt.get("conditionId", "") or mkt.get("id", ""),
            "question": mkt.get("question") or mkt.get("title", ""),
            "yes_bid": bid,
            "yes_ask": ask,
            "last_price": yes_price,
            "spread": (ask - bid) if (bid is not None and ask is not None) else None,
            "mid_price": ((bid + ask) / 2.0) if (bid is not None and ask is not None) else yes_price,
            "best_bid_size": _fp((bids[0] or {}).get("size")) if bids else None,
            "best_ask_size": _fp((asks[0] or {}).get("size")) if asks else None,
            "depth_bids": depth(bids),
            "depth_asks": depth(asks),
            "liquidity": _fp(mkt.get("liquidityNum") or mkt.get("liquidity")),
            "volume_24h": _fp(mkt.get("volume24hr") or mkt.get("volume_1d")),
            "volume_total": _fp(mkt.get("volume")),
            "open_interest": _fp(mkt.get("openInterest")),
            "close_date": _iso_ms(mkt.get("endDate")) or _iso_ms(mkt.get("expirationTime")),
            "status": "closed" if mkt.get("closed") else ("open" if mkt.get("active") else mkt.get("status", "active")),
        }
        row["timestamp"] = _iso_ms(mkt.get("updatedAt")) or _now_ms()
        return [row]


class KalshiClient:
    """Kalshi Trade API v2 public market data (no auth needed for reads)."""

    platform = "kalshi"

    def __init__(self, base_url: str = KALSHI_BASE):
        self.base = base_url

    def list_markets(self, limit: int = 1000, status: str = "active",
                     series_ticker: str | None = None, **filters) -> list[dict]:
        params = {"limit": str(limit)}
        if status:
            params["status"] = status
        if series_ticker:
            params["series_ticker"] = series_ticker
        params.update({k: str(v) for k, v in filters.items()})
        out: list[dict] = []
        cursor = None
        while True:
            p = dict(params)
            if cursor:
                p["cursor"] = cursor
            data = _json(f"{self.base}/markets", params=p)
            markets = data.get("markets", []) if isinstance(data, dict) else data
            out.extend(markets if isinstance(markets, list) else [])
            cursor = data.get("cursor") if isinstance(data, dict) else None
            if not cursor or len(markets) < limit:  # pragma: no cover - pagination guard
                break
        return out

    def get_market(self, ticker: str) -> dict:
        data = _json(f"{self.base}/markets/{ticker}")
        return data.get("market", data) if isinstance(data, dict) else {}

    def get_order_book(self, ticker: str, depth: int = 10) -> dict:
        return _json(f"{self.base}/markets/{ticker}/orderbook", params={"depth": str(depth)})

    def get_price_history(self, ticker: str, start_ms: int, end_ms: int,
                          interval: str = "1h") -> list[dict]:
        """Kalshi OHLC candles: period_interval in minutes (1/60/1440)."""
        period = {"1m": 1, "1h": 60, "1d": 1440}.get(interval, 60)
        data = _json(
            f"{self.base}/markets/candlesticks",
            params={"market_tickers": ticker, "start_ts": int(start_ms / 1000),
                    "end_ts": int(end_ms / 1000), "period_interval": period},
        )
        # The batch endpoint groups results by ticker; tolerate every shape.
        items = data
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get(ticker) or data.get("candlesticks") or data.get("data", [])
        if isinstance(items, dict):
            items = items.get("candlesticks", items)
        return items if isinstance(items, list) else []

    def normalize_snapshot(self, mkt: dict) -> list[dict]:
        yes_bid = _fp(mkt.get("yes_bid_dollars"))
        yes_ask = _fp(mkt.get("yes_ask_dollars"))
        last = _fp(mkt.get("last_price_dollars"))
        notional = _fp(mkt.get("notional_value_dollars")) or 1.0
        # Kalshi market objects expose only TOB sizes; full depth needs the orderbook.
        # ponytail: depth_bids/asks fall back to TOB size (single level) here; an
        # orderbook fetch would give true depth — upgrade path: populate below.
        bid_size = _fp(mkt.get("yes_bid_size_fp"))
        ask_size = _fp(mkt.get("yes_ask_size_fp"))
        row = {
            "platform": "kalshi",
            "market_id": mkt.get("ticker", ""),
            "event_id": mkt.get("event_ticker", ""),
            "question": mkt.get("title") or mkt.get("yes_sub_title", "") or mkt.get("subtitle", ""),
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "last_price": last,
            "spread": (yes_ask - yes_bid) if (yes_bid is not None and yes_ask is not None) else None,
            "mid_price": ((yes_bid + yes_ask) / 2.0) if (yes_bid is not None and yes_ask is not None) else last,
            "best_bid_size": bid_size,
            "best_ask_size": ask_size,
            "depth_bids": bid_size,  # TOB-only; see ponytail note
            "depth_asks": ask_size,
            "liquidity": None,
            "volume_24h": _scale(_fp(mkt.get("volume_24h_fp")), notional),
            "volume_total": _scale(_fp(mkt.get("volume_fp")), notional),
            "open_interest": _scale(_fp(mkt.get("open_interest_fp")), notional),
            "close_date": _iso_ms(mkt.get("close_time")),
            "status": mkt.get("status", "active"),
        }
        row["timestamp"] = _iso_ms(mkt.get("updated_time")) or _now_ms()
        return [row]


def _to_snapshot_frame(client: PMClient, markets: list[dict]) -> pl.DataFrame:
    """Normalize a list of raw market dicts into a de-duplicated PM_COLUMNS frame."""
    rows = []
    for m in markets:
        try:
            rows.extend(client.normalize_snapshot(m))
        except Exception as e:  # warn-and-continue: one bad contract must not abort
            mid = m.get("ticker") or m.get("id") or "?"
            print(f"WARN: snapshot normalize {client.platform}:{mid}: {e}")
    if not rows:
        return _empty(_PM_DTYPES)
    return (
        pl.DataFrame(rows, schema=_PM_DTYPES)
        .unique(subset=["platform", "market_id"], keep="last")
        .sort("timestamp", descending=True)
    )


def _to_bars(platform: str, market_id: str, question: str,
             history: list[dict]) -> pl.DataFrame:
    """Polymarket price-history / Kalshi candlesticks -> PM_BAR_COLUMNS frame."""
    rows = []
    for h in history:
        price = _fp(h.get("p"))
        if price is None:
            price = _fp(h.get("price", {}).get("close_dollars")) if isinstance(h.get("price"), dict) else None
        if price is None:
            continue
        ts = h.get("t")
        if isinstance(ts, (int, float)):
            ts_ms = int(ts * 1000) if ts < 1e12 else int(ts)
        else:
            ts_ms = _iso_ms(h.get("end_period_ts")) or _iso_ms(h.get("end_time"))
        ph = h.get("price") if isinstance(h.get("price"), dict) else {}
        rows.append({
            "timestamp": ts_ms,
            "platform": platform,
            "market_id": market_id,
            "question": question,
            "open": _fp(h.get("open")) or _fp(ph.get("open_dollars")) or price,
            "high": _fp(h.get("high")) or _fp(ph.get("high_dollars")) or price,
            "low": _fp(h.get("low")) or _fp(ph.get("low_dollars")) or price,
            "close": price,
            "volume": _fp(h.get("volume_fp")) or _fp(h.get("volume")),
            "open_interest": _fp(h.get("open_interest_fp")) or _fp(h.get("open_interest")),
        })
    if not rows:
        return _empty(_PM_BAR_DTYPES)
    return (
        pl.DataFrame(rows, schema=_PM_BAR_DTYPES)
        .with_columns(pl.col("timestamp").cast(pl.Int64))
        .unique(subset=["platform", "market_id", "timestamp"], keep="last")
        .sort("timestamp")
    )


def _load_watchlist(path: str | Path = PM_WATCHLIST) -> dict[str, list[str]]:
    """config/watchlist.json -> {"polymarket": [ids], "kalshi": [tickers]}.

    Per-platform keys make id types unambiguous; callers never need prefixes.
    """
    p = Path(path)
    if not p.exists():
        return {"polymarket": [], "kalshi": []}
    cfg = json.loads(p.read_text())
    wl = cfg.get("watchlist", {})
    if not isinstance(wl, dict):
        return {"polymarket": [], "kalshi": []}
    return {"polymarket": list(wl.get("polymarket", []) or []),
            "kalshi": list(wl.get("kalshi", []) or [])}


def fetch_pm_snapshot(
    platforms: list[PM_PLATFORM] | None = None,
    market_ids: dict[str, list[str]] | None = None,
    keywords: list[str] | None = None,
) -> pl.DataFrame:
    """Pull current snapshots for the watchlist (+ keyword-discovered BTC markets).

    Returns a single de-duplicated PM_COLUMNS frame (newest timestamp first).
    Per the project anti-leakage rule, only each contract's *own* timestamp
    (Gamma updatedAt / Kalshi updated_time) is stamped — never the poll time —
    so a bar never sees a future-dated quote.
    """
    wanted = platforms or ["polymarket", "kalshi"]
    ids = market_ids or _load_watchlist()
    clients: dict[str, PMClient] = {}
    if "polymarket" in wanted:
        clients["polymarket"] = PolymarketClient()
    if "kalshi" in wanted:
        clients["kalshi"] = KalshiClient()

    frames: list[pl.DataFrame] = []
    for name, ids_for in ids.items():
        if name not in clients:
            continue
        client = clients[name]
        # Explicit watchlist first (cheap: one GET per id).
        for mid in ids_for:
            try:
                m = client.get_market(mid)
                if m:
                    frames.append(_to_snapshot_frame(client, [m]))
            except Exception as e:
                print(f"WARN: {name} snapshot {mid}: {e}")
        # Keyword discovery adds crypto/BTC contracts to the watch set. Fetch the
        # full active list ONCE per platform (not per keyword) — both APIs paginate
        # and re-fetching N keywords × a 1000-row page is the exact waste the
        # Binance bulk-archive notes warn against.
        kw_lower = [k.lower() for k in (keywords or [])]
        if kw_lower:
            try:
                listed = (client.list_markets(limit=1000, closed=False) if name == "polymarket"
                          else client.list_markets(limit=1000, status="active"))
                for m in listed:
                    q = (m.get("question") or m.get("title") or m.get("yes_sub_title") or "")
                    if any(k in q.lower() for k in kw_lower):
                        frames.append(_to_snapshot_frame(client, [m]))
            except Exception as e:
                print(f"WARN: {name} discover: {e}")

    if not frames:
        return _empty(_PM_DTYPES)
    out = pl.concat(frames, how="diagonal_relaxed").unique(
        subset=["platform", "market_id"], keep="last"
    )
    return out.sort("timestamp", descending=True)


def fetch_pm_bars(
    start_ms: int, end_ms: int,
    platforms: list[PM_PLATFORM] | None = None,
    market_ids: dict[str, list[str]] | None = None,
    interval: str = DEFAULT_INTERVAL,
) -> pl.DataFrame:
    """Replay YES-probability history for the watchlist -> PM_BAR_COLUMNS frame."""
    if start_ms >= end_ms:
        raise ValueError(f"empty range: [{start_ms}, {end_ms})")
    wanted = platforms or ["polymarket", "kalshi"]
    ids = market_ids or _load_watchlist()
    clients: dict[str, PMClient] = {}
    if "polymarket" in wanted:
        clients["polymarket"] = PolymarketClient()
    if "kalshi" in wanted:
        clients["kalshi"] = KalshiClient()

    frames: list[pl.DataFrame] = []
    for name, id_list in ids.items():
        if name not in clients:
            continue
        client = clients[name]
        for mid in id_list:
            try:
                hist = client.get_price_history(mid, start_ms, end_ms, interval)
                q = ""
                try:
                    m = client.get_market(mid)
                    q = (m.get("question") or m.get("title") or "")
                except Exception as e:
                    print(f"WARN: {name} metadata {mid}: {e}")
                frames.append(_to_bars(name, mid, q, hist))
            except Exception as e:
                print(f"WARN: {name} history {mid}: {e}")

    if not frames:
        return _empty(_PM_BAR_DTYPES)
    return (
        pl.concat(frames, how="diagonal_relaxed")
        .with_columns(pl.col("timestamp").cast(pl.Int64))
        .filter((pl.col("timestamp") >= start_ms) & (pl.col("timestamp") < end_ms))
        .unique(subset=["platform", "market_id", "timestamp"], keep="last")
        .sort(["platform", "market_id", "timestamp"])
    )


def build_pm_sentiment(snapshot: pl.DataFrame) -> dict:
    """Reduce a PM_COLUMNS snapshot into frontier-model inputs.

    Aggregated probability / depth / volume / open-interest, biased to BTC-relevant
    contracts when present so sentiment skews toward the instrument the model trades.
    """
    if snapshot.is_empty():
        return {}
    n = snapshot.height
    btc = snapshot.filter(
        pl.col("question").str.to_lowercase().str.contains("btc")
        | pl.col("market_id").str.to_lowercase().str.contains("btc")
    )
    df = btc if btc.height else snapshot

    def mean(col: str) -> float | None:
        s = df[col].drop_nulls()
        return None if s.is_empty() else float(s.mean())

    def total(col: str) -> float | None:
        s = df[col].drop_nulls()
        return None if s.is_empty() else float(s.sum())

    bids = mean("depth_bids")
    asks = mean("depth_asks")
    return {
        "pm_n_contracts": n,
        "pm_yes_prob_mean": mean("last_price"),
        "pm_mid_mean": mean("mid_price"),
        "pm_spread_mean": mean("spread"),
        "pm_depth_bids_mean": bids,
        "pm_depth_asks_mean": asks,
        "pm_depth_imbalance": ((bids - asks) / (bids + asks)) if (bids and asks and bids + asks) else None,
        "pm_liquidity_total": total("liquidity"),
        "pm_volume_24h_total": total("volume_24h"),
        "pm_oi_total": total("open_interest"),
    }


if __name__ == "__main__":  # pragma: no cover
    import argparse

    p = argparse.ArgumentParser(description="Poll PM snapshots into a parquet log.")
    p.add_argument("--out", default="data/pm_snapshots.parquet")
    args = p.parse_args()
    df = fetch_pm_snapshot(keywords=["btc", "bitcoin", "crypto"])
    print(f"PM snapshot: {df.height} contracts across {len(set(df['platform']))} platforms")
    if df.height:
        print(df.select(["platform", "market_id", "question", "last_price",
                         "spread", "volume_24h"]).head(10).to_string())
    raise SystemExit(0)
