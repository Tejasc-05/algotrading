"""Minimal paper trading endpoints for the MVP.

These routes implement a lightweight paper account + order creation API and a
developer helper to materialize fills deterministically. The full production
engine (background processing, realtime events, advanced matching) is
implemented in later phases.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, status, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.api.dependencies import get_current_user, get_db
from app.database.models.user import User
from app.database.models.paper_trading import PaperAccount, Order, Trade
from app.schemas.trading import PaperAccountOut, PaperTradeOut, PaperTradingStartRequest
from app.database.models.enums import TradingMode, OrderStatus
from app.paper_trading import simulator

router = APIRouter(prefix="/paper-trading", tags=["paper-trading"])


@router.post("/start", response_model=PaperAccountOut, status_code=status.HTTP_201_CREATED)
async def start_paper_trading(
    payload: PaperTradingStartRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> PaperAccountOut:
    account = PaperAccount(
        user_id=current_user.id,
        strategy_id=payload.strategy_id,
        name=f"Paper Account {payload.strategy_id}",
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        starting_balance=payload.starting_balance,
        balance=payload.starting_balance,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    return PaperAccountOut(
        id=account.id,
        strategy_id=account.strategy_id,
        currency=account.currency,
        balance=float(account.balance),
        starting_balance=float(account.starting_balance),
        total_pnl=0.0,
        total_pnl_percent=0.0,
        is_active=account.is_active,
    )


class PaperOrderIn(BaseModel):
    paper_account_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float = Field(gt=0)
    price: Optional[float] = None


@router.post("/orders", status_code=status.HTTP_201_CREATED)
async def create_paper_order(
    payload: PaperOrderIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # ensure account belongs to user
    res = await db.execute(select(PaperAccount).where(PaperAccount.id == payload.paper_account_id))
    account = res.scalars().first()
    if not account or account.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Paper account not found")

    order = Order(
        user_id=current_user.id,
        strategy_id=account.strategy_id,
        paper_account_id=account.id,
        mode=TradingMode.PAPER,
        symbol=payload.symbol,
        side=payload.side,
        order_type=payload.order_type,
        quantity=payload.quantity,
        price=payload.price,
        status=OrderStatus.PENDING,
    )

    db.add(order)
    await db.commit()
    await db.refresh(order)

    return {"order_id": order.id, "status": order.status}


class SimulateFillIn(BaseModel):
    order_id: str
    current_price: float


@router.post("/simulate-fill", status_code=status.HTTP_200_OK)
async def simulate_fill_endpoint(
    payload: SimulateFillIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order_id = payload.order_id
    current_price = payload.current_price

    res = await db.execute(select(Order).where(Order.id == order_id))
    order = res.scalars().first()
    if not order or order.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Order not found")

    fill = simulator.simulate_fill(order, float(current_price))

    trade = Trade(
        order_id=order.id,
        executed_price=fill["executed_price"],
        executed_quantity=fill["executed_quantity"],
        fee=fill["fee"],
        pnl=fill.get("pnl"),
        executed_at=datetime.utcnow(),
    )
    db.add(trade)
    # mark order filled for MVP
    order.status = OrderStatus.FILLED
    await db.commit()
    await db.refresh(trade)

    return {
        "trade_id": trade.id,
        "order_id": order.id,
        "executed_price": float(trade.executed_price),
        "executed_quantity": float(trade.executed_quantity),
        "fee": float(trade.fee),
    }


class PaperTradingStopRequest(BaseModel):
    paper_account_id: Optional[str] = None


@router.post("/stop", response_model=PaperAccountOut)
async def stop_paper_trading(
    payload: Optional[PaperTradingStopRequest] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaperAccountOut:
    stmt = select(PaperAccount).where(PaperAccount.user_id == current_user.id, PaperAccount.is_active == True)
    if payload and payload.paper_account_id:
        stmt = stmt.where(PaperAccount.id == payload.paper_account_id)
    res = await db.execute(stmt)
    account = res.scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="No active paper account found")

    account.is_active = False
    await db.commit()
    await db.refresh(account)

    pnl = float(account.balance) - float(account.starting_balance)
    pnl_percent = (pnl / float(account.starting_balance) * 100) if float(account.starting_balance) else 0.0

    return PaperAccountOut(
        id=account.id,
        strategy_id=account.strategy_id,
        currency=account.currency,
        balance=float(account.balance),
        starting_balance=float(account.starting_balance),
        total_pnl=round(pnl, 2),
        total_pnl_percent=round(pnl_percent, 2),
        is_active=account.is_active,
    )


class PaperTradingTickRequest(BaseModel):
    paper_account_id: Optional[str] = None


@router.post("/tick")
async def run_paper_tick(
    payload: Optional[PaperTradingTickRequest] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.database.repositories.strategy_repository import StrategyRepository
    from app.schemas.strategy import StrategyGraph
    from app.paper_trading.engine import run_paper_trading_tick

    stmt = select(PaperAccount).where(PaperAccount.user_id == current_user.id, PaperAccount.is_active == True)
    if payload and payload.paper_account_id:
        stmt = stmt.where(PaperAccount.id == payload.paper_account_id)
    res = await db.execute(stmt)
    account = res.scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="No active paper account found")

    if not account.strategy_id:
        raise HTTPException(status_code=400, detail="Account has no associated strategy")

    repo = StrategyRepository(db)
    strategy = await repo.get(account.strategy_id)
    if not strategy or not strategy.versions:
        raise HTTPException(status_code=404, detail="Strategy not found")

    latest_version = max(strategy.versions, key=lambda v: v.version_number)
    graph = StrategyGraph.model_validate(latest_version.graph_json)

    result = await run_paper_trading_tick(
        db=db,
        account=account,
        strategy_graph=graph,
        symbol=account.symbol or "BTC/USDT",
        timeframe=account.timeframe or "1h",
    )
    return result


@router.get("/account", response_model=PaperAccountOut)
async def get_paper_account(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> PaperAccountOut:
    res = await db.execute(
        select(PaperAccount).where(PaperAccount.user_id == current_user.id, PaperAccount.is_active == True)
    )
    account = res.scalars().first()
    if not account:
        res2 = await db.execute(
            select(PaperAccount)
            .where(PaperAccount.user_id == current_user.id)
            .order_by(PaperAccount.created_at.desc())
        )
        account = res2.scalars().first()
        if not account:
            raise HTTPException(status_code=404, detail="No paper account found")

    pnl = float(account.balance) - float(account.starting_balance)
    pnl_percent = (pnl / float(account.starting_balance) * 100) if float(account.starting_balance) else 0.0

    return PaperAccountOut(
        id=account.id,
        strategy_id=account.strategy_id,
        currency=account.currency,
        balance=float(account.balance),
        starting_balance=float(account.starting_balance),
        total_pnl=round(pnl, 2),
        total_pnl_percent=round(pnl_percent, 2),
        is_active=account.is_active,
    )


@router.get("/trades", response_model=list[PaperTradeOut])
async def list_paper_trades(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[PaperTradeOut]:
    res = await db.execute(
        select(Trade)
        .options(joinedload(Trade.order))
        .join(Order)
        .where(Order.user_id == current_user.id)
        .order_by(Trade.executed_at.desc())
    )
    trades = res.scalars().all()
    return [
        PaperTradeOut(
            id=t.id,
            symbol=t.order.symbol,
            side=t.order.side.value if hasattr(t.order.side, "value") else str(t.order.side),
            status=t.order.status.value if hasattr(t.order.status, "value") else str(t.order.status),
            quantity=float(t.executed_quantity),
            price=float(t.executed_price),
            pnl=float(t.pnl) if t.pnl is not None else None,
            executed_at=t.executed_at,
        )
        for t in trades
    ]
