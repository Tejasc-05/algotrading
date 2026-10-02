"""Thin CCXT client wrapper (connection/instance management per exchange).

Returns a fully configured CCXTExchange instance ready for market data
and order calls. Supports all CCXT exchanges — Binance, Coinbase,
Kraken, Bybit included.
"""
import ccxt

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.exchanges.ccxt_service import CCXTExchange


def get_ccxt_client(exchange_id: str) -> CCXTExchange:
    """Create a CCXTExchange for the given exchange.

    In production, call with credentials from an ExchangeConnection ORM row:
        CCXTExchange.from_connection(connection)
    """
    settings = get_settings()

    api_key = None
    api_secret = None

    if exchange_id == "binance":
        api_key = settings.binance_api_key
        api_secret = settings.binance_secret

    return CCXTExchange(
        exchange_id=exchange_id,
        api_key=api_key,
        api_secret=api_secret,
        testnet=False,
    )