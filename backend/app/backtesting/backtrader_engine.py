"""Event-driven backtesting engine (same simulator, sequential evaluation)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from app.backtesting.simulator import simulate_strategy_on_ohlcv
from app.market_data.historical import fetch_historical_ohlcv


async def run_backtrader_backtest(
    strategy_graph: Any,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    starting_capital: float,
    fees_pct: float,
    slippage_pct: float,
    candles: pd.DataFrame | None = None,
) -> dict[str, Any]:
    df = candles
    if df is None:
        df = await fetch_historical_ohlcv(symbol=symbol, timeframe=timeframe, start=start, end=end)
    if df is None or df.empty:
        raise ValueError(f"No historical data available for {symbol} {timeframe}")

    return simulate_strategy_on_ohlcv(
        strategy_graph,
        df,
        symbol=symbol,
        timeframe=timeframe,
        starting_capital=starting_capital,
        fees_pct=fees_pct,
        slippage_pct=slippage_pct,
    )
