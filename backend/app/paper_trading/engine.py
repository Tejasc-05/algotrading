"""Simulated trading engine: evaluates an active strategy against market data.

Paper trading NEVER sends real orders to an exchange.
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.database.models.enums import OrderSide, OrderStatus, OrderType, TradingMode
from app.database.models.paper_trading import Order, PaperAccount, Trade
from app.market_data.historical import fetch_latest_ohlcv
from app.paper_trading.portfolio import apply_fill_to_portfolio
from app.paper_trading.simulator import simulate_fill
from app.strategy_engine.executor import StrategyExecutor

logger = get_logger(__name__)


async def run_paper_trading_tick(
    db: AsyncSession,
    account: PaperAccount,
    strategy_graph: Any,
    symbol: str,
    timeframe: str = "1h",
) -> dict[str, Any]:
    df = await fetch_latest_ohlcv(symbol=symbol, timeframe=timeframe, limit=120)
    if df is None or df.empty:
        raise AppError(
            code="MARKET_DATA_UNAVAILABLE",
            message="No market data available for paper trading",
            status_code=503,
        )

    current_price = float(df.iloc[-1]["close"])
    executor = StrategyExecutor(strategy_graph)
    signals = executor.evaluate(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": df,
            "current_price": current_price,
            "sentiment": None,
        }
    )

    actionable = next((s for s in signals if s.action in ("buy", "sell")), None)
    if actionable is None:
        return {"signal": None, "order": None, "message": "No trading signal generated", "price": current_price}

    side = actionable.action
    amount = float(actionable.params.get("amount", 0.1))
    order_type = str(actionable.params.get("type", "market"))
    max_affordable = max(float(account.balance) * 0.95 / current_price, 0)
    quantity = min(amount, max_affordable) if side == "buy" else amount
    if quantity <= 0:
        return {"signal": None, "order": None, "message": "Insufficient paper balance", "price": current_price}

    order = Order(
        user_id=account.user_id,
        strategy_id=account.strategy_id,
        paper_account_id=account.id,
        mode=TradingMode.PAPER,
        symbol=symbol,
        side=OrderSide(side),
        order_type=OrderType(order_type if order_type in ("market", "limit") else "market"),
        quantity=quantity,
        price=current_price,
        status=OrderStatus.PENDING,
    )
    db.add(order)
    await db.flush()

    fill = simulate_fill(order, current_price)
    portfolio_state = await apply_fill_to_portfolio(
        db, account, symbol=symbol, side=side, fill=fill
    )
    trade = Trade(
        order_id=order.id,
        executed_price=fill["executed_price"],
        executed_quantity=fill["executed_quantity"],
        fee=fill["fee"],
        pnl=fill.get("pnl"),
        executed_at=datetime.now(timezone.utc),
    )
    db.add(trade)
    order.status = OrderStatus.FILLED
    await db.commit()

    logger.info("Paper trade executed: %s %s %s at %s", side, quantity, symbol, current_price)
    return {
        "signal": {"node_id": actionable.node_id, "action": actionable.action, "params": actionable.params},
        "order": {"order_id": order.id, "status": order.status.value},
        "fill": fill,
        "price": current_price,
        "account": portfolio_state,
    }
