"""Request/response contracts for market data endpoints. Real implementation
lands in Phase 5 (CCXT integration) — routes currently return 501, but the
shapes are final so the frontend/team can build against them now."""
from datetime import datetime

from pydantic import BaseModel


class TickerOut(BaseModel):
    symbol: str
    price: float
    bid: float | None
    ask: float | None
    high_24h: float | None
    low_24h: float | None
    volume_24h: float | None
    timestamp: datetime


class OHLCVCandle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class OHLCVResponse(BaseModel):
    symbol: str
    timeframe: str
    candles: list[OHLCVCandle]
