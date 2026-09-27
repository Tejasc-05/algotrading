"""Realtime price/candle streaming from exchanges into Redis
(for the WebSocket manager to fan out to connected clients).

Architecture:
    Exchange → CCXT fetch_ohlcv/poll → Redis stream → WebSocketManager → frontend

Usage:
    asyncio.create_task(stream_realtime_prices(["BTC/USDT", "ETH/USDT"]))
"""
import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.redis.client import get_redis
from app.redis.pubsub import publish_event

logger = get_logger(__name__)

_settings = get_settings()

# Active streaming tasks
_streaming_tasks: dict[str, asyncio.Task] = {}


async def stream_realtime_prices(symbols: list[str]) -> None:
    """Stream real-time prices for given symbols into Redis.

    Each symbol is polled every `price_update_interval_seconds` seconds.
    Pushes `price_update` events to Redis pub/sub for WebSocket routing.
    """
    interval = getattr(_settings, "price_update_interval_seconds", 5)

    while True:
        for symbol in symbols:
            try:
                from app.exchanges.ccxt_service import CCXTExchange

                # Try Binance first
                exchange = CCXTExchange(exchange_id="binance", testnet=True)
                ticker = await exchange.fetch_ticker(symbol)

                payload = {
                    "symbol": symbol,
                    "price": float(ticker.get("last", 0)),
                    "bid": float(ticker.get("bid", 0)),
                    "ask": float(ticker.get("ask", 0)),
                    "volume": float(ticker.get("baseVolume", 0)),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                # Publish to Redis channel for this symbol
                await publish_event(f"price:{symbol}", payload)
                logger.debug(f"Published price for {symbol}: {payload['price']}")

            except Exception as e:
                logger.error(f"Failed to fetch ticker for {symbol}: {e}")

        await asyncio.sleep(interval)


async def start_streaming(symbols: list[str]) -> None:
    """Start streaming for the given symbols (idempotent)."""
    key = "|".join(sorted(symbols))
    if key in _streaming_tasks and not _streaming_tasks[key].done():
        return  # Already running

    task = asyncio.create_task(stream_realtime_prices(symbols))
    _streaming_tasks[key] = task
    logger.info(f"Started streaming for {symbols}")


def stop_streaming(symbols: list[str]) -> None:
    """Stop streaming for the given symbols."""
    key = "|".join(sorted(symbols))
    if key in _streaming_tasks:
        _streaming_tasks[key].cancel()
        del _streaming_tasks[key]
        logger.info(f"Stopped streaming for {symbols}")


async def stream_candles(
    symbol: str, timeframe: str, interval_seconds: int = 60
) -> None:
    """Stream OHLCV candles for a symbol/timeframe into Redis."""
    while True:
        try:
            from app.exchanges.ccxt_service import CCXTExchange

            exchange = CCXTExchange(exchange_id="binance", testnet=True)
            candles = await exchange._exchange.fetch_ohlcv(
                symbol=symbol, timeframe=timeframe, limit=1
            )

            if candles:
                candle = candles[-1]
                payload = {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": candle[0],
                    "open": candle[1],
                    "high": candle[2],
                    "low": candle[3],
                    "close": candle[4],
                    "volume": candle[5],
                }
                await publish_event(f"candle:{symbol}:{timeframe}", payload)

        except Exception as e:
            logger.error(f"Failed to fetch candle for {symbol}/{timeframe}: {e}")

        await asyncio.sleep(interval_seconds)