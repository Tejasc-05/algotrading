"""Historical OHLCV fetching + normalization into pandas DataFrames."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from app.core.logging import get_logger
from app.exchanges.ccxt_service import CCXTExchange

logger = get_logger(__name__)

TIMEFRAME_DELTA = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}

EXCHANGE_PRIORITY = ["binance", "bybit", "kraken"]


def normalize_ohlcv(df: pd.DataFrame | list) -> pd.DataFrame:
    """Return a frame with a `timestamp` column plus OHLCV, sorted ascending."""
    if isinstance(df, (list, tuple)):
        if len(df) > 0 and isinstance(df[0], dict):
            out = pd.DataFrame(df)
        elif len(df) > 0 and isinstance(df[0], (list, tuple)):
            cols = ["timestamp", "open", "high", "low", "close", "volume"]
            out = pd.DataFrame(df, columns=cols[: len(df[0])])
        else:
            out = pd.DataFrame(df)
    else:
        out = df.copy()

    if "timestamp" not in out.columns:
        out = out.reset_index()
        if "timestamp" not in out.columns:
            out = out.rename(columns={out.columns[0]: "timestamp"})

    # Parse timestamps (support existing datetimes, ms unix epoch, s epoch, or strings)
    if pd.api.types.is_datetime64_any_dtype(out["timestamp"]):
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    else:
        num_ts = pd.to_numeric(out["timestamp"], errors="coerce")
        if num_ts.notnull().all() and len(num_ts) > 0:
            first_val = float(num_ts.iloc[0])
            if first_val > 1e11:  # ms timestamp
                out["timestamp"] = pd.to_datetime(num_ts, unit="ms", utc=True)
            elif first_val > 1e8:  # seconds timestamp
                out["timestamp"] = pd.to_datetime(num_ts, unit="s", utc=True)
            else:
                out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
        else:
            out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)

    for col in ("open", "high", "low", "close", "volume"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        else:
            out[col] = np.nan

    out = out.dropna(subset=["close"]).sort_values("timestamp").reset_index(drop=True)
    return out[["timestamp", "open", "high", "low", "close", "volume"]]


def generate_synthetic_ohlcv(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    timeframe: str = "1h",
    limit: int = 200,
    start_price: float = 100.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Deterministic OHLCV used when live exchanges are unavailable (tests/offline)."""
    delta = TIMEFRAME_DELTA.get(timeframe, timedelta(hours=1))
    end_ts = end.astimezone(timezone.utc) if end and end.tzinfo else (end or datetime.now(timezone.utc))
    if end_ts.tzinfo is None:
        end_ts = end_ts.replace(tzinfo=timezone.utc)

    if start is not None:
        start_ts = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
        count = max(int((end_ts - start_ts) / delta), 50)
        count = min(count, 2000)
    else:
        count = max(limit, 50)
        start_ts = end_ts - delta * count

    rng = np.random.default_rng(seed)
    # Decline then recover so RSI < 30 can fire on a typical default strategy.
    decline = np.linspace(0, -0.35, count // 2)
    recover = np.linspace(-0.35, 0.05, count - len(decline))
    drift = np.concatenate([decline, recover])
    noise = rng.normal(0, 0.004, count)
    log_returns = np.diff(drift, prepend=drift[0]) + noise
    close = start_price * np.exp(np.cumsum(log_returns))
    close = np.maximum(close, 1.0)

    high = close * (1 + np.abs(rng.normal(0, 0.004, count)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, count)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = rng.uniform(10, 100, count)
    timestamps = [start_ts + i * delta for i in range(count)]

    return normalize_ohlcv(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": open_,
                "high": np.maximum(high, np.maximum(open_, close)),
                "low": np.minimum(low, np.minimum(open_, close)),
                "close": close,
                "volume": volume,
            }
        )
    )


def _public_exchange(exchange_id: str) -> CCXTExchange:
    return CCXTExchange(exchange_id=exchange_id, testnet=False)


async def fetch_historical_ohlcv(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> Optional[pd.DataFrame]:
    start_utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end_utc = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

    for exchange_id in EXCHANGE_PRIORITY:
        try:
            exchange = _public_exchange(exchange_id)
            since_ms = int(start_utc.timestamp() * 1000)
            end_ms = int(end_utc.timestamp() * 1000)
            all_ohlcv: list = []
            current_since = since_ms
            while current_since < end_ms:
                batch = exchange._exchange.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=timeframe,
                    since=current_since,
                    limit=1000,
                )
                if not batch:
                    break
                all_ohlcv.extend(batch)
                current_since = batch[-1][0] + 1
                if batch[-1][0] >= end_ms:
                    break
            if not all_ohlcv:
                continue
            df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df = normalize_ohlcv(df)
            df = df[(df["timestamp"] >= pd.Timestamp(start_utc)) & (df["timestamp"] <= pd.Timestamp(end_utc))]
            if not df.empty:
                logger.info("Fetched %s candles for %s from %s", len(df), symbol, exchange_id)
                return df
        except Exception as exc:
            logger.warning("Failed to fetch historical OHLCV from %s: %s", exchange_id, exc)
            continue

    logger.warning("Using synthetic OHLCV for %s %s", symbol, timeframe)
    return generate_synthetic_ohlcv(start=start_utc, end=end_utc, timeframe=timeframe)


async def fetch_latest_ohlcv(symbol: str, timeframe: str, limit: int = 100) -> Optional[pd.DataFrame]:
    for exchange_id in EXCHANGE_PRIORITY:
        try:
            exchange = _public_exchange(exchange_id)
            ohlcv = exchange._exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
            if not ohlcv:
                continue
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            return normalize_ohlcv(df)
        except Exception as exc:
            logger.warning("Failed to fetch latest OHLCV from %s: %s", exchange_id, exc)
            continue

    logger.warning("Using synthetic latest OHLCV for %s", symbol)
    return generate_synthetic_ohlcv(timeframe=timeframe, limit=limit)
