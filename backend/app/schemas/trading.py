"""Request/response contracts for paper trading, exchanges, and portfolio
endpoints. Real implementation lands in Phase 5 (exchanges) and Phase 7
(paper trading engine) — routes currently return 501."""
from datetime import datetime

from pydantic import BaseModel, Field

from app.database.models.enums import OrderSide, OrderStatus, PositionSide


class PaperTradingStartRequest(BaseModel):
    strategy_id: str
    symbol: str
    timeframe: str = "1h"
    starting_balance: float = Field(default=10_000, gt=0)


class PaperAccountOut(BaseModel):
    id: str
    strategy_id: str | None
    currency: str
    symbol: str | None = None
    timeframe: str | None = None
    balance: float
    starting_balance: float
    total_pnl: float
    total_pnl_percent: float
    is_active: bool


class PaperTradeOut(BaseModel):
    id: str
    symbol: str
    side: OrderSide
    status: OrderStatus
    quantity: float
    price: float | None
    pnl: float | None
    executed_at: datetime | None


class ExchangeConnectRequest(BaseModel):
    exchange_name: str = Field(description="CCXT exchange id, e.g. 'binance'")
    label: str | None = None
    api_key: str
    api_secret: str
    passphrase: str | None = None
    is_testnet: bool = True


class ExchangeConnectionOut(BaseModel):
    """Never includes api_key/api_secret — credentials are write-only."""

    id: str
    exchange_name: str
    label: str | None
    is_testnet: bool
    is_active: bool
    created_at: datetime


class PositionOut(BaseModel):
    id: str
    symbol: str
    side: PositionSide
    quantity: float
    average_entry_price: float
    current_price: float | None
    pnl: float | None
    pnl_percent: float | None


class PortfolioOut(BaseModel):
    total_value: float
    cash_balance: float
    daily_pnl: float
    daily_pnl_percent: float
    positions: list[PositionOut]


class EquityCurvePoint(BaseModel):
    date: datetime
    value: float


class PortfolioPerformanceOut(BaseModel):
    equity_curve: list[EquityCurvePoint]
    win_rate_pct: float | None
    sharpe_ratio: float | None
    max_drawdown_pct: float | None
