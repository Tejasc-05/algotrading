"""Market data endpoints — CCXT-backed ticker and OHLCV data."""
from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_user
from app.core.exceptions import AppError
from app.database.models.user import User
from app.exchanges.ccxt_service import CCXTExchange
from app.market_data.historical import EXCHANGE_PRIORITY, fetch_latest_ohlcv
from app.schemas.market import OHLCVCandle, OHLCVResponse, TickerOut

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/ticker", response_model=TickerOut)
async def get_ticker(
    symbol: str = Query(..., examples=["BTC/USDT"]),
    current_user: User = Depends(get_current_user),
) -> TickerOut:
    last_error: Exception | None = None
    for exchange_id in EXCHANGE_PRIORITY:
        try:
            exchange = CCXTExchange(exchange_id=exchange_id, testnet=False)
            ticker = await exchange.fetch_ticker(symbol)
            return TickerOut(
                symbol=ticker.symbol,
                price=ticker.last_price,
                bid=ticker.bid,
                ask=ticker.ask,
                high_24h=ticker.high_24h,
                low_24h=ticker.low_24h,
                volume_24h=ticker.volume_24h,
                timestamp=ticker.timestamp,
            )
        except Exception as exc:
            last_error = exc
            continue

    df = await fetch_latest_ohlcv(symbol=symbol, timeframe="1h", limit=2)
    if df is not None and not df.empty:
        last = df.iloc[-1]
        return TickerOut(
            symbol=symbol,
            price=float(last.close),
            bid=None,
            ask=None,
            high_24h=float(last.high),
            low_24h=float(last.low),
            volume_24h=float(last.volume),
            timestamp=last.timestamp.to_pydatetime(),
        )
    raise AppError(
        code="MARKET_DATA_UNAVAILABLE",
        message=f"No market data available for symbol '{symbol}'" + (f": {last_error}" if last_error else ""),
        status_code=503,
    )


@router.get("/ohlcv", response_model=list[OHLCVCandle])
async def get_ohlcv(
    symbol: str = Query(..., examples=["BTC/USDT"]),
    timeframe: str = Query("1h", examples=["1h"]),
    limit: int = Query(100, le=1000),
    current_user: User = Depends(get_current_user),
) -> list[OHLCVCandle]:
    df = await fetch_latest_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
    if df is None or df.empty:
        raise AppError(
            code="MARKET_DATA_UNAVAILABLE",
            message=f"No OHLCV data available for symbol '{symbol}'",
            status_code=503,
        )

    candles = [
        OHLCVCandle(
            timestamp=row.timestamp.to_pydatetime() if hasattr(row.timestamp, "to_pydatetime") else row.timestamp,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in df.itertuples()
    ]
    return candles
