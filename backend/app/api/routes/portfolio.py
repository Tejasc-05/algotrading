"""Portfolio aggregation endpoints. Real implementation (Phase 7+) returns
user-level aggregated balances and P/L across all active paper accounts."""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user, get_db
from app.core.exceptions import AppError
from app.database.models.paper_trading import PaperAccount, Trade, Order
from app.schemas.trading import PortfolioOut, PositionOut, PortfolioPerformanceOut

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.database.models.user import User

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("", response_model=PortfolioOut)
async def get_portfolio(
    current_user: User,
    db: AsyncSession = Depends(get_db),
) -> PortfolioOut:
    """Get user's portfolio summary across all paper accounts."""
    from sqlalchemy import select

    # Get all active paper accounts for this user
    accounts_res = await db.execute(
        select(PaperAccount).where(
            PaperAccount.user_id == current_user.id, PaperAccount.is_active == True
        )
    )
    accounts = accounts_res.scalars().all()

    total_cash = 0.0
    total_pnl = 0.0
    all_positions: list[PositionOut] = []

    for account in accounts:
        # Get all filled trades for this account
        from sqlalchemy import select as sa_select

        trades_res = await db.execute(
            select(Trade).join(Order).where(
                Order.user_id == current_user.id,
                Order.paper_account_id == account.id,
                Order.status == "filled",
            )
        )
        trades = trades_res.scalars().all()

        # Get position per symbol
        position_map: dict[str, dict[str, Any]] = {}
        for trade in trades:
            side = trade.order.side
            symbol = trade.order.symbol
            executed_qty = float(trade.executed_quantity)
            executed_price = float(trade.executed_price)

            if symbol not in position_map:
                position_map[symbol] = {
                    "quantity": 0.0,
                    "total_cost": 0.0,
                    "realized_pnl": 0.0,
                    "side": side,
                }

            if side == "buy":
                position_map[symbol]["quantity"] += executed_qty
                position_map[symbol]["total_cost"] += executed_price * executed_qty
            elif side == "sell":
                position_map[symbol]["quantity"] -= executed_qty

        for symbol, pm in position_map.items():
            qty = pm["quantity"]
            if qty != 0:
                avg_entry = pm["total_cost"] / max(1, abs(qty))
                all_positions.append(
                    PositionOut(
                        id=f"pos_{symbol}",
                        symbol=symbol,
                        side=pm["side"] if qty > 0 else ("short" if qty < 0 else "long"),
                        quantity=abs(qty),
                        average_entry_price=round(avg_entry, 2),
                        current_price=None,
                        pnl=None,
                        pnl_percent=None,
                    )
                )
            total_pnl += pm.get("realized_pnl", 0.0)

        total_cash += float(account.balance)

    total_value = total_cash + total_pnl

    return PortfolioOut(
        total_value=round(total_value, 2),
        cash_balance=round(total_cash, 2),
        daily_pnl=round(total_pnl, 2),
        daily_pnl_percent=round((total_pnl / total_cash * 100) if total_cash else 0.0, 2),
        positions=all_positions,
    )


@router.get("/performance", response_model=PortfolioPerformanceOut)
async def get_portfolio_performance(
    current_user: User,
    db: AsyncSession = Depends(get_db),
) -> PortfolioPerformanceOut:
    """Get portfolio performance analytics."""
    from sqlalchemy import select, func

    # Get all filled trades across all active accounts
    from sqlalchemy import select as sa_select

    trades_res = await db.execute(
        select(Trade).join(Order).where(
            Order.user_id == current_user.id,
            Order.paper_account_id.isnot(None),
            Order.status == "filled",
        )
    )
    trades = trades_res.scalars().all()

    if not trades:
        return PortfolioPerformanceOut(
            equity_curve=[],
            win_rate_pct=None,
            sharpe_ratio=None,
            max_drawdown_pct=None,
        )

    # Get all active accounts
    from sqlalchemy import select as sa_select2
    from app.database.models.paper_trading import PaperAccount as PA

    accounts_res = await db.execute(
        select(PA).where(PA.user_id == current_user.id, PA.is_active == True)
    )
    accounts = accounts_res.scalars().all()

    starting_total = sum(float(a.starting_balance) for a in accounts)

    equity_curve: list[dict[str, Any]] = []
    all_pnls: list[float] = []

    for trade in trades:
        pnl = float(trade.pnl) if trade.pnl is not None else 0.0
        all_pnls.append(pnl)
        equity_curve.append({
            "timestamp": trade.executed_at,
            "value": starting_total + sum(all_pnls),
        })

    import numpy as np

    win_rate_pct = None
    sharpe_ratio = None
    max_drawdown_pct = None

    if all_pnls:
        wins = [p for p in all_pnls if p > 0]
        losses = [p for p in all_pnls if p < 0]
        win_rate_pct = (len(wins) / len(all_pnls) * 100) if len(all_pnls) else None

        if len(all_pnls) > 1:
            returns = np.array(all_pnls)
            mean_ret = float(np.mean(returns))
            std_ret = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0
            sharpe_ratio = round((mean_ret / std_ret) if std_ret > 0 else None, 4) if std_ret > 0 else None

        if len(equity_curve) > 1:
            values = [p["value"] for p in equity_curve]
            running_max = values[0]
            max_dd = 0.0
            for v in values[1:]:
                if v > running_max:
                    running_max = v
                dd = (v - running_max) / running_max * 100
                max_dd = min(max_dd, dd)
            max_drawdown_pct = round(abs(max_dd), 2) if max_dd < 0 else 0.0

    return PortfolioPerformanceOut(
        equity_curve=equity_curve,
        win_rate_pct=round(win_rate_pct, 2) if win_rate_pct is not None else None,
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct,
    )