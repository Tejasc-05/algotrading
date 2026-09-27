"""Historical OHLCV fetching + normalization into pandas DataFrames for the
backtesting engines and strategy execution.
"""
import pandas as pd
from datetime import datetime, timezone
from typing import List, Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.exchanges.ccxt_service import CCXTExchange
from app.market_data.ccxt_client import get_ccxt_client

logger = get_logger(__name__)


async def fetch_historical_ohlcv(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> Optional[pd.DataFrame]:
    """
    Fetch historical OHLCV data for a symbol/timeframe range.

    Returns a pandas DataFrame with columns: timestamp, open, high, low, close, volume
    indexed by timestamp (UTC).
    """
    settings = get_settings()
    logger.debug(f"Fetching historical OHLCV: {symbol} {timeframe} {start} -> {end}")

    # Try exchanges in priority order: Binance, Coinbase, Kraken, Bybit
    exchange_priority = ["binance", "coinbasepro", "kraken", "bybit"]

    for exchange_id in exchange_priority:
        try:
            client = get_ccxt_client(exchange_id)
            exchange = CCXTExchange(
                exchange_id=exchange_id,
                testnet=True,
            )
            since_ms = int(start.timestamp() * 1000)
            end_ms = int(end.timestamp() * 1000)

            # Fetch in chunks if needed
            all_ohlcv = []
            current_since = since_ms
            limit = 1000  # CCXT default limit

            while current_since < end_ms:
                ohlcv = client._exchange.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=timeframe,
                    since=current_since,
                    limit=limit,
                )
                if not ohlcv:
                    break
                all_ohlcv.extend(ohlcv)
                current_since = ohlcv[-1][0] + 1  # Next timestamp

                # Stop if we've reached the end
                if ohlcv[-1][0] >= end_ms:
                    break

            if not all_ohlcv:
                continue

            # Convert to DataFrame
            df = pd.DataFrame(
                all_ohlcv,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df.set_index("timestamp", inplace=True)
            df.sort_index(inplace=True)

            # Filter to exact range
            df = df[(df.index >= start) & (df.index <= end)]

            if not df.empty:
                logger.debug(f"Successfully fetched {len(df)} candles from {exchange_id}")
                return df

        except Exception as e:
            logger.warning(f"Failed to fetch from {exchange_id}: {e}")
            continue

    logger.error(f"Failed to fetch OHLCV for {symbol} from any exchange")
    return None


async def fetch_latest_ohlcv(
    symbol: str, timeframe: str, limit: int = 100
) -> Optional[pd.DataFrame]:
    """Fetch the most recent candles for strategy execution."""
    try:
        # Try Binance first (deepest liquidity)
        client = get_ccxt_client("binance")
        ohlcv = client._exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
        if not ohlcv:
            return None

        df = pd.DataFrame(
            ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df
    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"Failed to fetch latest OHLCV: {e}")
        return None