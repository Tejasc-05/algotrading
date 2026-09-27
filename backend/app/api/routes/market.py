"""Market data endpoints — CCXT-backed ticker and OHLCV data."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_db
from app.core.config import get_settings
from app.core.exceptions import AppError, NotFoundError
from app.database.models.user import User
from app.exchanges.ccxt_service import CCXTExchange
from app.market_data.ccxt_client import get_ccxt_client
from app.market_data.historical import fetch_latest_ohlcv
from app.schemas.market import OHLCVCandle, OHLCVResponse, TickerOut

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/ticker", response_model=TickerOut)
async def get_ticker(
    symbol: str = Query(..., examples=["BTC/USDT"]),
    current_user: User = Depends(get_current_user),
) -> TickerOut:
    """Fetch the latest ticker for a symbol from the best available exchange."""
    try:
        # Prefer Binance for deep liquidity, fall back to others
        for exchange_id in ["binance", "coinbasepro", "kraken", "bybit"]:
            try:
                exchange = CCXTExchange(exchange_id=exchange_id, testnet=True)
                ticker = await exchange.fetch_ticker(symbol)
                return ticker
            except Exception:
                continue
        raise AppError(
            code="MARKET_DATA_UNAVAILABLE",
            message=f"No market data available for symbol '{symbol}'",
            status_code=503,
        )
    except AppError:
        raise
    except Exception as e:
        raise AppError(
            code="MARKET_DATA_ERROR",
            message=f"Failed to fetch market data: {str(e)}",
            status_code=500,
        )


@router.get("/ohlcv", response_model=OHLCVResponse)
async def get_ohlcv(
    symbol: str = Query(..., examples=["BTC/USDT"]),
    timeframe: str = Query("1h", examples=["1h"]),
    limit: int = Query(100, le=1000),
    current_user: User = Depends(get_current_user),
) -> OHLCVResponse:
    """Fetch recent OHLCV candles for a symbol/timeframe."""
    try:
        df = await fetch_latest_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
        if df is None or df.empty:
            raise AppError(
                code="MARKET_DATA_UNAVAILABLE",
                message=f"No OHLCV data available for symbol '{symbol}'",
                status_code=503,
            )

        candles = [
            OHLCVCandle(
                timestamp=row.timestamp,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
            )
            for row in df.itertuples()
        ]

        return OHLCVResponse(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
        )
    except AppError:
        raise
    except Exception as e:
        raise AppError(
            code="MARKET_DATA_ERROR",
            message=f"Failed to fetch OHLCV data: {str(e)}",
            status_code=500,
        )