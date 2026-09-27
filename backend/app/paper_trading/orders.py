"""Simulated order creation/matching for paper trading.

Paper trading NEVER sends real orders to an exchange. All orders are
simulated and stored in the database for tracking.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.database.models.enums import OrderSide, OrderStatus, OrderType, TradingMode
from app.database.models.paper_trading import Order, PaperAccount

logger = get_logger(__name__)


async def create_simulated_order(
    paper_account_id: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float] = None,
    db: Optional[AsyncSession] = None,
) -> dict:
    """Create a simulated paper order. Never touches a real exchange.

    Returns a dict with order details.
    """
    if db is None:
        raise ValueError("Database session is required")

    # Verify the paper account exists
    result = await db.execute(select(PaperAccount).where(PaperAccount.id == paper_account_id))
    account = result.scalars().first()
    if account is None:
        raise ValueError(f"Paper account not found: {paper_account_id}")

    # Create order
    order = Order(
        user_id=account.user_id,
        strategy_id=account.strategy_id,
        paper_account_id=paper_account_id,
        mode=TradingMode.PAPER,
        symbol=symbol,
        side=OrderSide(side.lower()),
        order_type=OrderType(order_type.lower()),
        quantity=quantity,
        price=price,
        status=OrderStatus.PENDING,
    )

    db.add(order)
    await db.commit()
    await db.refresh(order)

    logger.info(
        f"Paper order created: {side} {quantity} {symbol} "
        f"({'market' if order_type == 'market' else f'limit @ {price}'}) "
        f"account={paper_account_id}"
    )

    return {
        "order_id": order.id,
        "symbol": order.symbol,
        "side": order.side.value,
        "order_type": order.order_type.value,
        "quantity": float(order.quantity),
        "price": float(order.price) if order.price else None,
        "status": order.status.value,
        "created_at": order.created_at.isoformat(),
    }