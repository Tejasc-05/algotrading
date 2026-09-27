"""Simulated trading engine: evaluates an active strategy against live
market data and drives simulated order creation.

Paper trading NEVER sends real orders to an exchange. All orders are
simulated fills using the simulator.
"""
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.database.models.enums import OrderSide, OrderStatus, TradingMode
from app.market_data.ccxt_client import get_ccxt_client
from app.paper_trading.orders import create_simulated_order
from app.paper_trading.simulator import simulate_fill

logger = get_logger(__name__)


async def run_paper_trading_tick(
    paper_account_id: str,
    strategy_graph: Any,
    symbol: str,
    timeframe: str = "1h",
) -> dict[str, Any]:
    """Evaluate a strategy against the latest market data and create
    a simulated order if a signal is generated.

    Returns a dict with signal info, order result, and updated account state.
    """
    settings = get_settings()

    # 1. Fetch latest market data
    try:
        client = get_ccxt_client("binance")
        candles_df = await client._exchange.fetch_ohlcv(
            symbol=symbol, timeframe=timeframe, limit=100
        )
    except Exception as e:
        logger.error(f"Failed to fetch market data for paper trading: {e}")
        raise AppError(
            code="MARKET_DATA_ERROR",
            message=f"Failed to fetch market data: {str(e)}",
            status_code=503,
        )

    if not candles_df:
        raise AppError(
            code="MARKET_DATA_UNAVAILABLE",
            message="No market data available for paper trading",
            status_code=503,
        )

    # 2. Build market context for strategy execution
    from app.strategy_engine.executor import StrategyExecutor

    executor = StrategyExecutor(strategy_graph)
    latest_candle = candles_df[-1]

    market_context = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": pd.DataFrame(
            candles_df,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        ),
        "current_price": float(latest_candle[4]),  # close price
        "sentiment": None,  # Sentiment will be added when integrated
    }

    # 3. Execute strategy against market data
    signals = executor.evaluate(market_context)

    if not signals:
        return {
            "signal": None,
            "order": None,
            "message": "No trading signal generated",
        }

    # 4. Process signals — find first actionable BUY/SELL signal
    for signal in signals:
        if signal.action in ("buy", "sell"):
            side = signal.action
            params = signal.params

            amount = float(params.get("amount", 0.1))
            order_type = params.get("type", "market")

            # 5. Create simulated order (NEVER a real exchange order)
            order_result = await create_simulated_order(
                paper_account_id=paper_account_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=amount,
                price=market_context["current_price"] if order_type == "limit" else None,
            )

            # 6. Simulate fill
            fill = simulate_fill(
                order=type("MockOrder", (), {"side": side, "quantity": amount})(),
                current_price=market_context["current_price"],
            )

            logger.info(
                f"Paper trade executed: {side} {amount} {symbol} at {market_context['current_price']}"
            )

            return {
                "signal": {
                    "node_id": signal.node_id,
                    "action": signal.action,
                    "params": params,
                },
                "order": order_result,
                "fill": fill,
                "price": market_context["current_price"],
            }

    return {
        "signal": None,
        "order": None,
        "message": "No actionable BUY/SELL signal found",
    }


async def calculate_pnl(
    paper_account_id: str,
    symbol: str,
    entry_price: float,
    current_price: float,
    quantity: float,
    side: str,
) -> dict[str, Any]:
    """Calculate unrealized P/L for a position."""
    if side == "long":
        pnl = (current_price - entry_price) * quantity
        pnl_percent = ((current_price - entry_price) / entry_price) * 100
    else:  # short
        pnl = (entry_price - current_price) * quantity
        pnl_percent = ((entry_price - current_price) / entry_price) * 100

    return {
        "pnl": round(pnl, 2),
        "pnl_percent": round(pnl_percent, 2),
        "current_price": current_price,
    }