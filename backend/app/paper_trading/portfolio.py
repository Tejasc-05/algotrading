"""Paper-account cash + position bookkeeping. Never talks to an exchange."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.enums import OrderSide, OrderStatus, PositionSide
from app.database.models.paper_trading import Order, PaperAccount, Trade
from app.database.models.portfolio import Portfolio, Position


async def _ensure_portfolio(db: AsyncSession, account: PaperAccount) -> Portfolio:
    result = await db.execute(select(Portfolio).where(Portfolio.paper_account_id == account.id))
    portfolio = result.scalars().first()
    if portfolio is None:
        portfolio = Portfolio(
            user_id=account.user_id,
            paper_account_id=account.id,
            name=f"Paper {account.symbol}",
        )
        db.add(portfolio)
        await db.flush()
    return portfolio


async def apply_fill_to_portfolio(
    db: AsyncSession,
    account: PaperAccount,
    *,
    symbol: str,
    side: str,
    fill: dict[str, Any],
) -> dict[str, Any]:
    portfolio = await _ensure_portfolio(db, account)
    result = await db.execute(
        select(Position).where(
            Position.portfolio_id == portfolio.id,
            Position.symbol == symbol,
            Position.is_open.is_(True),
        )
    )
    position = result.scalars().first()

    qty = float(fill["executed_quantity"])
    price = float(fill["executed_price"])
    fee = float(fill.get("fee") or 0.0)
    side_val = side.lower()
    pnl = 0.0

    if side_val == "buy":
        cost = price * qty + fee
        account.balance = float(account.balance) - cost
        if position is None:
            position = Position(
                portfolio_id=portfolio.id,
                symbol=symbol,
                side=PositionSide.LONG,
                quantity=qty,
                average_entry_price=price,
                is_open=True,
                opened_at=datetime.now(timezone.utc),
            )
            db.add(position)
        else:
            existing = float(position.quantity)
            avg = float(position.average_entry_price)
            new_qty = existing + qty
            position.average_entry_price = ((avg * existing) + (price * qty)) / new_qty if new_qty else price
            position.quantity = new_qty
        fill["pnl"] = None
    else:
        proceeds = price * qty - fee
        account.balance = float(account.balance) + proceeds
        if position is not None:
            existing = float(position.quantity)
            closed = min(existing, qty)
            pnl = (price - float(position.average_entry_price)) * closed - fee
            remaining = existing - closed
            if remaining <= 1e-12:
                position.quantity = 0
                position.is_open = False
                position.closed_at = datetime.now(timezone.utc)
            else:
                position.quantity = remaining
        fill["pnl"] = pnl

    await db.flush()
    return {
        "cash_balance": float(account.balance),
        "pnl": fill.get("pnl"),
        "positions": await list_open_positions(db, account),
    }


async def list_open_positions(db: AsyncSession, account: PaperAccount) -> list[Position]:
    result = await db.execute(select(Portfolio).where(Portfolio.paper_account_id == account.id))
    portfolio = result.scalars().first()
    if portfolio is None:
        return []
    pos_res = await db.execute(
        select(Position).where(Position.portfolio_id == portfolio.id, Position.is_open.is_(True))
    )
    return list(pos_res.scalars().all())


async def realized_pnl(db: AsyncSession, account: PaperAccount) -> float:
    result = await db.execute(
        select(Trade)
        .join(Order)
        .options(selectinload(Trade.order))
        .where(Order.paper_account_id == account.id, Order.status == OrderStatus.FILLED)
    )
    trades = result.scalars().all()
    return sum(float(t.pnl) for t in trades if t.pnl is not None)
